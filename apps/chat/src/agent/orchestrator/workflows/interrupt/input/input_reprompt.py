"""Input-interrupt reprompt helpers."""

from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import build_cancellation_reset_updates
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _current_task_types, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_input import (
    _build_compact_transfer_input_reprompt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    INPUT_INTERRUPT_MAX_ATTEMPTS,
    _input_interrupt_required_fields,
)
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.network_utils import format_network_display_name


def _input_greeting_reprompt_text(
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str] | None = None,
) -> str:
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    required_fields = _input_interrupt_required_fields(interrupt)
    task_types = current_task_types or _current_task_types(state, getattr(interrupt, "task_ids", []))
    first_task = None
    task_ids = getattr(interrupt, "task_ids", []) or []
    if isinstance(task_ids, list) and task_ids:
        first_task = state.tasks.get(str(task_ids[0]))
    first_payload = first_task.payload if first_task and isinstance(first_task.payload, dict) else {}
    network = format_network_display_name(first_payload.get("network"))
    if task_types == {"transfer"}:
        if transfer_reprompt := _build_compact_transfer_input_reprompt(state, interrupt):
            return transfer_reprompt
    if required_fields == {"data_plan_id"}:
        if task_types == {"data"} and network:
            return render_message(
                "orchestrator.execution.input_greeting_data_plan_network",
                locale,
                {"network": network},
            )
        return render_message("orchestrator.execution.input_greeting_data_plan", locale)
    if required_fields == {"data_plan_preference"} and task_types == {"data"}:
        if network:
            return render_message(
                "orchestrator.execution.input_greeting_data_preference_network",
                locale,
                {"network": network},
            )
        return render_message("orchestrator.execution.input_greeting_data_preference", locale)
    if task_types == {"airtime"}:
        if required_fields == {"amount"}:
            if network:
                return render_message(
                    "orchestrator.execution.input_greeting_airtime_amount_network",
                    locale,
                    {"network": network},
                )
            return render_message("orchestrator.execution.input_greeting_airtime_amount", locale)
        if required_fields in ({"recipient_phone", "amount"}, {"phone", "amount"}):
            if network:
                return render_message(
                    "orchestrator.execution.input_greeting_airtime_line_amount_network",
                    locale,
                    {"network": network},
                )
            return render_message("orchestrator.execution.input_greeting_airtime_line_amount", locale)
    return render_message("orchestrator.execution.input_greeting", locale)


async def _reprompt_or_reset_updates(
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
    *,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    if getattr(interrupt, "kind", None) != "input":
        return _reprompt_updates(state, interrupt)

    next_attempts = max(int(getattr(interrupt, "attempts", 0) or 0) + 1, 1)
    if next_attempts < INPUT_INTERRUPT_MAX_ATTEMPTS:
        next_interrupt = interrupt.model_copy(update={"attempts": next_attempts})
        if prompt_override:
            return {
                "pending_interrupt": next_interrupt,
                "last_interrupt": next_interrupt,
                "tasks": state.tasks,
                "outbox": [{"type": "say", "text": prompt_override}],
            }
        return _reprompt_updates(state, next_interrupt)

    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    response_text = render_message("orchestrator.execution.input_attempts_exhausted", locale)
    logger.info(
        "interrupt_input_attempt_budget_exhausted",
        task_ids=getattr(interrupt, "task_ids", None),
        attempts=next_attempts,
    )
    return {
        **reset_updates,
        "last_interrupt": interrupt,
        "outbox": [{"type": "say", "text": response_text}],
        "final_response": response_text,
    }


__all__ = [
    "_input_greeting_reprompt_text",
    "_reprompt_or_reset_updates",
]
