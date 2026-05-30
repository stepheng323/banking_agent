"""Authorization reason copy for pending transaction prompts."""

from __future__ import annotations

from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message


def format_auth_reason(task_type: str, locale: str = "en") -> str:
    """Format the authorization reason based on task type."""
    key_by_task: dict[str, MessageKey] = {
        "transfer": "orchestrator.execution.auth_reason_transfer",
        "airtime": "orchestrator.execution.auth_reason_airtime",
        "data": "orchestrator.execution.auth_reason_data",
    }
    key = key_by_task.get(task_type, "orchestrator.execution.auth_reason_default")
    return render_message(key, locale)
