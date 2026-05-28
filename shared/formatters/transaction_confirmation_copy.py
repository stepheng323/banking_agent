"""Confirmation and completion copy for transaction tasks."""

from __future__ import annotations

from collections.abc import Iterable

from shared.formatters.transaction_copy_context import derive_task_mix
from shared.i18n.personality import PersonalityContext, render_personalized_message
from shared.i18n.renderer import render_message


def build_confirmation_header(
    *,
    task_types: Iterable[str],
    locale: str,
    task_count: int,
    task_actions: Iterable[str] | None = None,
    personality_context: PersonalityContext | None = None,
) -> str:
    """Build a context-aware confirmation header."""
    if task_count > 1:
        return render_message("transaction_copy.confirmation.mixed", locale)

    mix = derive_task_mix(task_types)
    actions = {str(action or "").strip() for action in (task_actions or [])}
    if actions & {"edit_scheduled_transaction"}:
        return render_message("transaction_copy.confirmation.schedule_update", locale)
    if mix == "transfer":
        if actions & {"schedule_transfer", "recurring_transfer"}:
            return render_message("transaction_copy.confirmation.scheduled_transfer", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.transfer",
            locale,
            context=personality_context,
        )
    if mix == "airtime":
        if actions & {"schedule_airtime", "recurring_airtime"}:
            return render_message("transaction_copy.confirmation.scheduled_airtime", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.airtime",
            locale,
            context=personality_context,
        )
    if mix == "data":
        if actions & {"schedule_data", "recurring_data"}:
            return render_message("transaction_copy.confirmation.scheduled_data", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.data",
            locale,
            context=personality_context,
        )
    return render_message("transaction_copy.confirmation.generic", locale)


def build_confirmation_section_label(task_type: str, *, locale: str) -> str:
    """Return the short section label for one task inside a mixed confirmation."""
    normalized = str(task_type or "").strip().lower()
    if normalized not in {"transfer", "airtime", "data"}:
        normalized = "generic"
    return render_message(f"transaction_copy.confirmation.section.{normalized}", locale)


def format_confirmation_section(*, task_type: str, summary: str, locale: str) -> str:
    """Prefix a task summary with a readable label for mixed confirmations."""
    cleaned = str(summary or "").strip()
    if not cleaned:
        return ""
    label = build_confirmation_section_label(task_type, locale=locale).strip()
    if not label:
        return cleaned
    return f"{label}\n{cleaned}"


def build_completion_frame(*, task_types: Iterable[str], locale: str, task_count: int) -> tuple[str, str]:
    """Return completion header/footer copy for the task composition."""
    mix = derive_task_mix(task_types)
    plural = task_count > 1

    if mix == "transfer":
        header_key = (
            "transaction_copy.completion.header.transfer_plural"
            if plural
            else "transaction_copy.completion.header.transfer"
        )
        footer_key = (
            "transaction_copy.completion.footer.transfer_plural"
            if plural
            else "transaction_copy.completion.footer.transfer"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    if mix == "airtime":
        header_key = (
            "transaction_copy.completion.header.airtime_plural"
            if plural
            else "transaction_copy.completion.header.airtime"
        )
        footer_key = (
            "transaction_copy.completion.footer.airtime_plural"
            if plural
            else "transaction_copy.completion.footer.airtime"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    if mix == "data":
        header_key = (
            "transaction_copy.completion.header.data_plural"
            if plural
            else "transaction_copy.completion.header.data"
        )
        footer_key = (
            "transaction_copy.completion.footer.data_plural"
            if plural
            else "transaction_copy.completion.footer.data"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    return (
        render_message("transaction_copy.completion.header.mixed", locale),
        render_message("transaction_copy.completion.footer.mixed", locale),
    )
