"""Localized conversational closure helpers for orchestrator presentation."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.i18n.renderer import render_message

_TRANSACTION_INTENTS = {"transfer", "airtime", "data"}


def _intent_label(intent: str | None, locale: str) -> str:
    normalized = (intent or "").strip().lower()
    if normalized == "transfer":
        return render_message("orchestrator.closure.intent.transfer", locale)
    if normalized == "airtime":
        return render_message("orchestrator.closure.intent.airtime", locale)
    if normalized == "data":
        return render_message("orchestrator.closure.intent.data", locale)
    return render_message("orchestrator.closure.intent.transaction", locale)


def _target_action(target_intent: str | None, locale: str) -> str:
    normalized = (target_intent or "").strip().lower()
    if normalized == "account":
        return render_message("orchestrator.closure.detour.action.account", locale)
    if normalized == "query":
        return render_message("orchestrator.closure.detour.action.query", locale)
    if normalized == "support":
        return render_message("orchestrator.closure.detour.action.support", locale)
    if normalized == "faq":
        return render_message("orchestrator.closure.detour.action.faq", locale)
    if normalized == "beneficiary":
        return render_message("orchestrator.closure.detour.action.beneficiary", locale)
    if normalized in _TRANSACTION_INTENTS:
        return render_message(
            "orchestrator.closure.detour.action.transaction",
            locale,
            {"intent": _intent_label(normalized, locale)},
        )
    return render_message("orchestrator.closure.detour.action.generic", locale)


def _state_locale(state: Any) -> str:
    loaded_context = getattr(state, "loaded_context", None)
    if isinstance(loaded_context, dict):
        locale = loaded_context.get("language")
        if isinstance(locale, str) and locale.strip():
            return locale
    return "en"


def build_detour_pause_notice(
    *,
    active_intent: str | None,
    target_intent: str | None,
    locale: str,
) -> str | None:
    if (active_intent or "").strip().lower() not in _TRANSACTION_INTENTS:
        return None
    return render_message(
        "orchestrator.closure.detour.paused_while",
        locale,
        {
            "paused_intent": _intent_label(active_intent, locale),
            "target_action": _target_action(target_intent, locale),
        },
    )


def build_detour_pause_notice_for_state(
    *,
    state: Any,
    active_intent: str | None,
    target_intent: str | None,
) -> str | None:
    return build_detour_pause_notice(
        active_intent=active_intent,
        target_intent=target_intent,
        locale=_state_locale(state),
    )


def build_resume_declined_notice(*, intent: str | None, locale: str) -> str:
    return render_message(
        "orchestrator.closure.resume.declined",
        locale,
        {"intent": _intent_label(intent, locale)},
    )


def _first_snapshot_value(snapshot: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = snapshot.get(key)
        if value not in (None, ""):
            return value
    return None


def _same_value(left: Any, right: Any) -> bool:
    if left in (None, "") and right in (None, ""):
        return True
    return str(left).strip().casefold() == str(right).strip().casefold()


def _format_amount(value: Any) -> str:
    try:
        return format_amount_compact(value)
    except Exception:
        return str(value)


def build_edit_update_notice(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any] | None,
    locale: str,
) -> str | None:
    if not previous_snapshot or not current_snapshot:
        return None

    previous_amount = _first_snapshot_value(previous_snapshot, ("amount", "Amount"))
    current_amount = _first_snapshot_value(current_snapshot, ("amount", "Amount"))
    if current_amount not in (None, "") and not _same_value(previous_amount, current_amount):
        return render_message(
            "orchestrator.closure.edit.amount",
            locale,
            {"amount": _format_amount(current_amount)},
        )

    previous_source = _first_snapshot_value(previous_snapshot, ("sourceBank", "source_bank_name", "source_bank"))
    current_source = _first_snapshot_value(current_snapshot, ("sourceBank", "source_bank_name", "source_bank"))
    if current_source not in (None, "") and not _same_value(previous_source, current_source):
        return render_message(
            "orchestrator.closure.edit.source_account",
            locale,
            {"bank": str(current_source)},
        )

    previous_recipient = _first_snapshot_value(
        previous_snapshot,
        ("recipientName", "recipient_name", "recipient_resolved_name", "target"),
    )
    current_recipient = _first_snapshot_value(
        current_snapshot,
        ("recipientName", "recipient_name", "recipient_resolved_name", "target"),
    )
    if current_recipient not in (None, "") and not _same_value(previous_recipient, current_recipient):
        return render_message(
            "orchestrator.closure.edit.recipient",
            locale,
            {"recipient": str(current_recipient)},
        )

    previous_phone = _first_snapshot_value(previous_snapshot, ("phone", "target_phone", "recipient_phone"))
    current_phone = _first_snapshot_value(current_snapshot, ("phone", "target_phone", "recipient_phone"))
    if current_phone not in (None, "") and not _same_value(previous_phone, current_phone):
        return render_message(
            "orchestrator.closure.edit.phone",
            locale,
            {"phone": str(current_phone)},
        )

    previous_network = _first_snapshot_value(previous_snapshot, ("network",))
    current_network = _first_snapshot_value(current_snapshot, ("network",))
    if current_network not in (None, "") and not _same_value(previous_network, current_network):
        return render_message(
            "orchestrator.closure.edit.network",
            locale,
            {"network": str(current_network)},
        )

    previous_plan = _first_snapshot_value(previous_snapshot, ("plan_name", "planName"))
    current_plan = _first_snapshot_value(current_snapshot, ("plan_name", "planName"))
    if current_plan not in (None, "") and not _same_value(previous_plan, current_plan):
        return render_message(
            "orchestrator.closure.edit.data_plan",
            locale,
            {"plan": str(current_plan)},
        )

    return render_message("orchestrator.closure.edit.generic", locale)


__all__ = [
    "build_detour_pause_notice",
    "build_detour_pause_notice_for_state",
    "build_edit_update_notice",
    "build_resume_declined_notice",
]
