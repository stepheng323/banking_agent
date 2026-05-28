"""Auth reprompt outbox rendering."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale
from shared.formatters.auth_reason import format_auth_reason
from shared.i18n.renderer import render_message


def _auth_header_for_task_ids(state: OrchestratorState, task_ids: list[str], locale: str) -> str:
    task_types = {state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks}
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


def _build_auth_reprompt_outbox(state: OrchestratorState, interrupt: Any) -> list[dict[str, Any]]:
    if not interrupt.task_ids:
        return []
    first_task = state.tasks.get(interrupt.task_ids[0])
    if not first_task:
        return []

    locale = _state_locale(state)
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
            "header": _auth_header_for_task_ids(state, interrupt.task_ids, locale),
            "summary": summary,
            "actionable_payload": build_actionable_payload_for_tasks(
                [state.tasks[task_id] for task_id in interrupt.task_ids if task_id in state.tasks]
            ),
        }
    ]


__all__ = [
    "_auth_header_for_task_ids",
    "_build_auth_reprompt_outbox",
]
