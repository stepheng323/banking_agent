"""Task builders for interrupt switch turns."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _next_interrupt_task_id, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_bill_seeds import (
    _seed_airtime_switch_payload,
    _seed_data_switch_payload,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_transfer_seed import (
    _seed_transfer_switch_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_expand import (
    _expand_underproduced_transfer_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_reconcile import (
    _reconcile_multi_transfer_recipient_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.operations import operation_spec
from banking.support.classifier import classify_support_intent_deterministic
from shared.types.balance import initial_balance_contract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    AccountLifecycleOperation,
    ScheduleQueryContract,
)
from shared.types.planner import (
    InterruptRouteDecision,
    PlannerTaskParameters,
    ScheduleTaskParameters,
    dump_task_parameters,
    make_planned_task,
)
from shared.types.read import ReadRequest

_CANONICAL_SCHEDULE_ACTIONS = {
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
    "pause_scheduled_transaction",
    "resume_scheduled_transaction",
    "list_scheduled_runs",
    "find_scheduled_run",
}


def _canonicalize_switch_planner_task(
    *,
    action: str,
    target_intent: str,
    parameters: PlannerTaskParameters,
) -> tuple[str, str, PlannerTaskParameters]:
    planner_action = action
    if target_intent != "transfer" or planner_action not in _CANONICAL_SCHEDULE_ACTIONS:
        return planner_action, target_intent, parameters

    dumped_parameters = dump_task_parameters(parameters)
    schedule_parameters = ScheduleTaskParameters(
        **{
            field_name: dumped_parameters[field_name]
            for field_name in ScheduleTaskParameters.model_fields
            if field_name in dumped_parameters
        }
    )
    return planner_action, "schedule", schedule_parameters


async def _build_enriched_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    interrupt: Any,
    services: OrchestrationServices,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    parameters: PlannerTaskParameters
    payload_seed: dict[str, Any]
    action: str
    preseeded: bool
    if target_intent == "transfer":
        parameters, payload_seed, action, preseeded = await _seed_transfer_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )
    elif target_intent == "airtime":
        parameters, payload_seed, action, preseeded = await _seed_airtime_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )
    else:
        parameters, payload_seed, action, preseeded = await _seed_data_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )

    base_task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)
    planner_action, planner_executor, planner_parameters = _canonicalize_switch_planner_task(
        action=action,
        target_intent=target_intent,
        parameters=parameters,
    )
    planned_tasks = [
        make_planned_task(
            task_id=base_task_id,
            action=planner_action,
            executor=cast(Any, planner_executor),
            instruction=text,
            parameters=planner_parameters,
            risk=operation_spec(planner_executor, planner_action).risk,
        )
    ]

    if target_intent == "transfer" and action == "send_money":
        planned_tasks, _ = _expand_underproduced_transfer_tasks(planned_tasks, text)
        planned_tasks, reconcile_meta = _reconcile_multi_transfer_recipient_tasks(planned_tasks, text)
        if reconcile_meta:
            logger.info(
                "interrupt_transfer_multi_recipient_reconcile_applied",
                recipient_count=reconcile_meta["recipient_count"],
                recipient_names=reconcile_meta["recipient_names"],
                changed_tasks=reconcile_meta["changed_tasks"],
            )

    payload_overrides_by_task_id: dict[str, dict[str, Any]] = {
        task.task_id: {"message": text} for task in planned_tasks
    }
    if action != planner_action:
        payload_overrides_by_task_id[planned_tasks[0].task_id]["action"] = action
    if len(planned_tasks) == 1 and payload_seed:
        payload_overrides_by_task_id[planned_tasks[0].task_id].update(payload_seed)
    new_tasks, waves = build_task_specs_and_waves_from_plan_items(
        planned_tasks,
        text,
        preserve_existing_action_instruction=True,
        include_skip_extraction=preseeded,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
        payload_overrides_by_task_id=payload_overrides_by_task_id,
    )
    return new_tasks, waves, {target_intent}


def _build_direct_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)
    task = TaskSpec(
        id=task_id,
        type=cast(Any, target_intent),
        stage=TaskStage.DRAFT,
        payload={
            "instruction": text,
            "message": text,
        },
    )
    return {task_id: task}, [[task_id]], {target_intent}


def _build_direct_non_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    route: InterruptRouteDecision,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    if target_intent == "account" and route.account_read is not None:
        read_request = route.account_read
        balance_contract = None
        lifecycle_contract = None
        if read_request.subject == "balance":
            balance_contract = initial_balance_contract(
                bank_name=read_request.bank_name,
                response_shape=read_request.response_shape,
            )
        elif read_request.subject in {"linked_account", "default_account"}:
            lifecycle_operation: AccountLifecycleOperation = (
                "default_identity" if read_request.subject == "default_account" else "list"
            )
            if read_request.subject == "linked_account":
                if read_request.response_shape == "fact_bool":
                    lifecycle_operation = "existence"
                elif read_request.response_shape == "fact_count":
                    lifecycle_operation = "count"
                elif read_request.response_shape == "fact_status":
                    lifecycle_operation = "readiness"
                elif read_request.response_shape == "surface_detail":
                    lifecycle_operation = "detail"
            lifecycle_contract = AccountLifecycleContract(
                operation=lifecycle_operation,
                response_shape=read_request.response_shape,
                bank_name=read_request.bank_name,
                mandate_statuses=[read_request.status] if read_request.status else [],
            )
        task_id, task = _build_direct_domain_task(
            state_view=cast(Any, interrupt_state_view(state)),
            domain="account",
            mode="new",
            message_text=text,
            read_request=read_request,
            balance_contract=balance_contract,
            account_lifecycle_contract=lifecycle_contract,
        )
        return {task_id: task}, [[task_id]], {target_intent}

    if target_intent == "schedule":
        task_id, task = _build_direct_domain_task(
            state_view=cast(Any, interrupt_state_view(state)),
            domain="schedule",
            mode=route.target_mode,
            message_text=text,
            read_request=ReadRequest(subject="schedule", response_shape="surface_list"),
            schedule_contract=ScheduleQueryContract(),
        )
        return {task_id: task}, [[task_id]], {target_intent}

    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)

    payload: dict[str, Any] = {
        "instruction": text,
        "message": text,
    }
    if target_intent == "query":
        payload["message"] = text
        payload["force_new_query"] = route.target_mode != "continuation"
    elif target_intent == "support":
        deterministic = classify_support_intent_deterministic(text)
        if deterministic is not None and deterministic.intent is not None and deterministic.confidence >= 0.85:
            payload["intent"] = deterministic.intent.value
    elif target_intent == "account" and route.account_action not in {None, "none", "unknown"}:
        payload["action"] = route.account_action

    task = TaskSpec(
        id=task_id,
        type=cast(Any, target_intent),
        depends_on=[],
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return {task_id: task}, [[task_id]], {target_intent}


__all__ = [
    "_build_direct_non_transaction_switch_tasks",
    "_build_direct_transaction_switch_tasks",
    "_build_enriched_transaction_switch_tasks",
]
