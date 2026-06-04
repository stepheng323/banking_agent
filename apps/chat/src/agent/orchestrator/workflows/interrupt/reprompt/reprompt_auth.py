"""Auth reprompt outbox rendering."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale_for_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import (
    InterruptStateView,
    interrupt_state_view,
)
from banking.presentation.formatters.auth_reason import format_auth_reason
from banking.presentation.i18n.renderer import render_message


def _auth_header_for_task_ids(state: OrchestratorState, task_ids: list[str], locale: str) -> str:
    return _auth_header_for_task_ids_for_view(interrupt_state_view(state), task_ids, locale)


def _auth_header_for_task_ids_for_view(state_view: InterruptStateView, task_ids: list[str], locale: str) -> str:
    task_types = state_view.task_types_for(task_ids)
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


def _build_auth_reprompt_outbox(state: OrchestratorState, interrupt: Any) -> list[dict[str, Any]]:
    state_view = interrupt_state_view(state)
    if not interrupt.task_ids:
        return []
    first_task = state_view.task(interrupt.task_ids[0])
    if not first_task:
        return []

    locale = _state_locale_for_view(state_view)
    summary = interrupt.prompt or first_task.payload.get("confirmation", {}).get(
        "summary",
        render_message("orchestrator.execution.pin_prompt_default", locale),
    )
    return [
        {
            "type": "auth_request",
            "method": interrupt.auth_method or "pin",
            "task_ids": interrupt.task_ids,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "header": _auth_header_for_task_ids_for_view(state_view, interrupt.task_ids, locale),
            "summary": summary,
            "actionable_payload": build_actionable_payload_for_tasks(state_view.tasks_for(interrupt.task_ids)),
        }
    ]


__all__ = [
    "_auth_header_for_task_ids",
    "_auth_header_for_task_ids_for_view",
    "_build_auth_reprompt_outbox",
]
