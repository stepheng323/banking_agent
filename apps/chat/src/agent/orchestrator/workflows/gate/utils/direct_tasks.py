"""Direct task construction for gate-owned shortcuts."""

from typing import Any, Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import (
    derive_transfer_schedule_fields,
    infer_schedule_action_from_text,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.read_only_response import (
    classify_read_only_response_shape,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import DIRECT_DOMAIN_ACTIONS
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView
from banking.policy.service import capability_block_message


def _next_direct_account_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_account_balance"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_account_balance_{idx}"
    return task_id


def _next_direct_query_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_query"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_query_{idx}"
    return task_id


def _next_direct_domain_task_id(existing_tasks: dict[str, TaskSpec], domain: str) -> str:
    idx = 1
    task_id = f"direct_{domain}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_{domain}_{idx}"
    return task_id


def _next_direct_beneficiary_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_beneficiary_save"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_beneficiary_save_{idx}"
    return task_id


def _direct_task_response_shape(
    *,
    state_view: GateStateView,
    message_text: str,
    schedule_response_mode: Literal["list", "count"] | None,
) -> str | None:
    if schedule_response_mode == "count":
        return "fact_count"
    if schedule_response_mode == "list":
        return "surface_list"
    return classify_read_only_response_shape(
        message_text,
        loaded_context=state_view.loaded_context_or_empty,
        context_frames=state_view.context_frames,
    )


def _build_direct_domain_task(
    *,
    state_view: GateStateView,
    domain: Literal["query", "account", "support", "beneficiary", "transfer", "airtime", "data", "schedule", "faq"],
    mode: str | None = None,
    schedule_response_mode: Literal["list", "count"] | None = None,
    message_text: str | None = None,
) -> tuple[str, TaskSpec]:
    if domain == "query":
        task_id = _next_direct_query_task_id(state_view.tasks)
    else:
        task_id = _next_direct_domain_task_id(state_view.tasks, domain)

    task_message = state_view.last_message_text if message_text is None else message_text
    payload: dict[str, Any] = {
        "message": task_message,
        "instruction": task_message,
    }
    response_shape = _direct_task_response_shape(
        state_view=state_view,
        message_text=task_message or "",
        schedule_response_mode=schedule_response_mode,
    )
    if response_shape:
        payload["response_shape"] = response_shape
    if domain == "query":
        if mode == "new":
            payload["force_new_query"] = True
    elif domain in {"transfer", "airtime", "data"}:
        schedule_message_text = task_message or ""
        inferred_schedule_action = infer_schedule_action_from_text(schedule_message_text)
        if inferred_schedule_action:
            domain_schedule_action = {
                "transfer": inferred_schedule_action,
                "airtime": "recurring_airtime"
                if inferred_schedule_action == "recurring_transfer"
                else "schedule_airtime",
                "data": "recurring_data" if inferred_schedule_action == "recurring_transfer" else "schedule_data",
            }[domain]
            payload["action"] = domain_schedule_action
            payload.update(
                derive_transfer_schedule_fields(
                    schedule_message_text,
                    schedule_text=None,
                    scheduled_text=None,
                    recurring_flag=domain_schedule_action.startswith("recurring_"),
                )
            )
    elif domain == "beneficiary":
        payload["action"] = "list_beneficiaries"
        payload["intent"] = "list_beneficiaries"
        payload["list_intent"] = True
        if response_shape == "fact_count":
            payload["count_intent"] = True
        elif response_shape == "fact_bool":
            payload["existence_intent"] = True
    elif domain == "schedule":
        payload["action"] = "list_scheduled_transactions"
        if schedule_response_mode in {"list", "count"}:
            payload["schedule_response_mode"] = schedule_response_mode
    elif domain == "faq":
        payload["action"] = "answer_question"

    spec = TaskSpec(
        id=task_id,
        type=domain,
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return task_id, spec


def _direct_domain_capability_block_message(
    state_view: GateStateView,
    domain: str,
) -> str | None:
    action = DIRECT_DOMAIN_ACTIONS.get(domain)
    if not action:
        return None
    return capability_block_message(domain=domain, action=action, locale=_current_locale(state_view))
