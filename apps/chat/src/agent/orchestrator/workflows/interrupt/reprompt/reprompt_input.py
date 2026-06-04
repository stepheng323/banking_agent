"""Input interrupt reprompt rendering."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale_for_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.formatters.recipient_display import format_recipient_display_label
from banking.presentation.formatters.recipient_prompt_names import sanitize_recipient_display_name
from banking.presentation.i18n.renderer import render_message


def _build_compact_transfer_input_reprompt(state: OrchestratorState, interrupt: Any) -> str | None:
    state_view = interrupt_state_view(state)
    if getattr(interrupt, "kind", None) != "input":
        return None
    task_ids = getattr(interrupt, "task_ids", None)
    if not isinstance(task_ids, list) or not task_ids:
        return None

    first_task_id = str(task_ids[0])
    task = state_view.task(first_task_id)
    if not task or task.type != "transfer":
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return None
    required_fields_raw = fields_by_task.get(first_task_id, [])
    required_fields = [field for field in required_fields_raw if isinstance(field, str)]
    required_set = set(required_fields)
    transfer_fields = {"recipient_account", "recipient_bank_name"}
    if not required_set or not required_set.issubset(transfer_fields):
        return None

    locale = _state_locale_for_view(state_view)
    task_payload = task.payload if isinstance(task.payload, dict) else {}
    recipient_label = format_recipient_display_label(
        task_payload.get("recipient_name"),
        task_payload.get("recipient_resolved_name"),
    )
    safe_recipient = sanitize_recipient_display_name(recipient_label, locale)

    if required_set == transfer_fields:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number_and_bank",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_account"}:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_bank_name"}:
        return cast(str, render_message("response.templates.ask_bank", locale))

    return None


__all__ = ["_build_compact_transfer_input_reprompt"]
