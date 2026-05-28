"""Recipient labels for execution prompts."""

from typing import Any

from shared.formatters.recipient_display import format_recipient_display_label


def _recipient_prompt_label(task_payload: dict[str, Any]) -> str | None:
    """Build recipient display label for prompts: alias first, resolved name in parentheses."""
    recipient_name = task_payload.get("recipient_name")
    resolved_name = task_payload.get("recipient_resolved_name")
    return format_recipient_display_label(
        str(recipient_name) if isinstance(recipient_name, str) else None,
        str(resolved_name) if isinstance(resolved_name, str) else None,
    )


__all__ = ["_recipient_prompt_label"]
