"""Task builders for interrupt switch turns."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _next_interrupt_task_id, logger
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
from shared.types.planner import InterruptRouteDecision, PlannedTask


async def _build_enriched_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    interrupt: Any,
    services: OrchestrationServices,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
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
    planned_tasks = [
        PlannedTask(
            task_id=base_task_id,
            action=action,
            executor=cast(Any, target_intent),
            instruction=text,
            parameters=parameters,
            risk="MONEY_MOVE",
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
    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)

    payload: dict[str, Any] = {
        "instruction": text,
        "message": text,
    }
    if target_intent == "query":
        payload["message"] = text
        payload["force_new_query"] = route.target_mode != "continuation"

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
