"""Confirmation reprompt outbox rendering."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale_for_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.formatters.confirmation import build_confirmation_summary
from banking.presentation.formatters.transaction_confirmation_copy import build_confirmation_header
from banking.presentation.i18n.personality import transfer_personality_context_from_payload


def _build_confirmation_reprompt_outbox(
    state: OrchestratorState,
    interrupt: Any,
    task_ids: list[str],
) -> list[dict[str, Any]]:
    state_view = interrupt_state_view(state)
    if not task_ids:
        return []
    first_task = state_view.task(task_ids[0])
    if not first_task:
        return []

    locale = _state_locale_for_view(state_view)
    summary = interrupt.prompt
    if not summary:
        accounts_raw = state_view.loaded_context_or_empty.get("accounts") or []
        accounts = [account for account in accounts_raw if isinstance(account, dict)]
        if len(task_ids) == 1:
            summary = build_confirmation_summary(
                task_payload=first_task.payload,
                locale=locale,
                accounts=accounts,
            )
        else:
            parts: list[str] = []
            for task_id in task_ids:
                task = state_view.task(task_id)
                if not task:
                    continue
                rendered = build_confirmation_summary(task_payload=task.payload, locale=locale, accounts=accounts)
                if rendered:
                    parts.append(rendered)
            summary = "\n\n".join(parts)
    if not summary:
        return []

    confirmation_payload = first_task.payload.get("confirmation") or {}
    snapshot = confirmation_payload.get("snapshot") or {}
    confirmation_personality_context = None
    if len(task_ids) == 1 and first_task.type == "transfer":
        confirmation_personality_context = transfer_personality_context_from_payload(
            first_task.payload,
            moment="confirmation",
        )
    return [
        {
            "type": "request_confirmation",
            "task_ids": task_ids,
            "header": build_confirmation_header(
                task_types=state_view.task_type_list_for(task_ids),
                locale=locale,
                task_count=len(task_ids),
                task_actions=state_view.task_action_list_for(task_ids),
                personality_context=confirmation_personality_context,
            ),
            "summary": summary,
            "snapshot": snapshot,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "actionable_payload": build_actionable_payload_for_tasks(state_view.tasks_for(task_ids)),
        }
    ]


__all__ = ["_build_confirmation_reprompt_outbox"]
