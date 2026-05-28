"""Per-task confirmation summary rendering helpers."""

from typing import Any

from shared.formatters.confirmation import (
    append_source_account_info,
    build_confirmation_summary,
    build_source_account_info,
)
from shared.formatters.transaction_intent_lines import format_intent_line


def _strip_batch_name_mismatch_warning(summary: str, task_payload: dict[str, Any]) -> str:
    """Remove verbose name-mismatch warning from batch confirmations while preserving the core summary."""
    warning = task_payload.get("name_mismatch_warning")
    if not isinstance(warning, str) or not warning.strip():
        return summary

    cleaned = summary.strip()
    warning_text = warning.strip()
    if not cleaned:
        return ""

    if cleaned == warning_text:
        return ""

    for pattern in (f"{warning_text}\n\n", f"{warning_text}\n"):
        if cleaned.startswith(pattern):
            return cleaned[len(pattern) :].strip()

    if cleaned.startswith(warning_text):
        return cleaned[len(warning_text) :].lstrip("\n").strip()

    return cleaned


def _render_task_confirmation_summary(
    *,
    task: Any,
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    canonical = build_confirmation_summary(
        task_payload=task.payload,
        locale=locale,
        accounts=accounts,
    )
    if canonical:
        cleaned = _strip_batch_name_mismatch_warning(canonical, task.payload)
        if cleaned:
            return cleaned

    confirmation_payload = task.payload.get("confirmation") or {}
    raw_summary = confirmation_payload.get("summary")
    if isinstance(raw_summary, str):
        cleaned = _strip_batch_name_mismatch_warning(raw_summary, task.payload)
        if cleaned:
            source_account_info = build_source_account_info(
                task_payload=task.payload,
                snapshot=confirmation_payload.get("snapshot")
                if isinstance(confirmation_payload.get("snapshot"), dict)
                else {},
                accounts=accounts,
                locale=locale,
            )
            return append_source_account_info(cleaned, source_account_info, locale=locale)

    return format_intent_line(task.type, task.payload, locale=locale)


__all__ = [
    "_render_task_confirmation_summary",
    "_strip_batch_name_mismatch_warning",
]
