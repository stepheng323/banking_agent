"""Helpers for visible transaction confirmation edit acknowledgements."""

from __future__ import annotations

import re
from typing import Any

from shared.formatters.currency import format_naira
from shared.i18n.renderer import render_message
from shared.utils.network_utils import format_network_display_name


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _normalize_digits(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\D+", "", str(value))


def _amount_changed(previous: Any, current: Any) -> bool:
    try:
        previous_amount = round(float(previous), 2)
        current_amount = round(float(current), 2)
    except (TypeError, ValueError):
        return previous != current
    return previous_amount != current_amount


def _text_changed(previous: Any, current: Any) -> bool:
    return _normalize_text(previous) != _normalize_text(current)


def _digits_changed(previous: Any, current: Any) -> bool:
    return _normalize_digits(previous) != _normalize_digits(current)


def _snapshot_value(snapshot: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in snapshot:
            return snapshot.get(key)
    return None


def _formatted_amount(value: Any) -> str:
    try:
        return format_naira(float(value))
    except (TypeError, ValueError):
        return ""


def build_airtime_confirmation_update_message(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    locale: str,
) -> str | None:
    """Return a concise visible update message for airtime confirmation edits."""

    if not previous_snapshot:
        return None

    changed: list[str] = []
    if _amount_changed(previous_snapshot.get("amount"), current_snapshot.get("amount")):
        changed.append("amount")
    if _digits_changed(previous_snapshot.get("recipient_phone"), current_snapshot.get("recipient_phone")):
        changed.append("recipient_phone")
    if _text_changed(previous_snapshot.get("network"), current_snapshot.get("network")):
        changed.append("network")
    previous_source = _snapshot_value(previous_snapshot, "source_account_number", "source_account")
    current_source = _snapshot_value(current_snapshot, "source_account_number", "source_account")
    if previous_source is not None and _digits_changed(previous_source, current_source):
        changed.append("source_account")

    if not changed:
        return None
    if changed == ["amount"]:
        amount = _formatted_amount(current_snapshot.get("amount"))
        if amount:
            return render_message("airtime.confirmation.change.amount_to", locale, {"amount": amount})
    if changed == ["recipient_phone"]:
        phone = str(current_snapshot.get("recipient_phone") or "").strip()
        if phone:
            return render_message("airtime.confirmation.change.phone_to", locale, {"phone": phone})
    if changed == ["network"]:
        network = format_network_display_name(current_snapshot.get("network"))
        if network:
            return render_message("airtime.confirmation.change.network_to", locale, {"network": network})
    if changed == ["source_account"]:
        source = _source_label(current_snapshot)
        if source:
            return render_message("airtime.confirmation.change.source_to", locale, {"source": source})
    return render_message("airtime.confirmation.change.details", locale)


def build_data_confirmation_update_message(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    locale: str,
) -> str | None:
    """Return a concise visible update message for data confirmation edits."""

    if not previous_snapshot:
        return None

    changed: list[str] = []
    if _amount_changed(previous_snapshot.get("amount"), current_snapshot.get("amount")):
        changed.append("amount")
    if _text_changed(previous_snapshot.get("network"), current_snapshot.get("network")):
        changed.append("network")
    if _digits_changed(previous_snapshot.get("target_phone"), current_snapshot.get("target_phone")):
        changed.append("target_phone")
    if _text_changed(previous_snapshot.get("plan_code"), current_snapshot.get("plan_code")) or _text_changed(
        previous_snapshot.get("plan_name"), current_snapshot.get("plan_name")
    ):
        changed.append("plan")
    previous_source = _snapshot_value(previous_snapshot, "source_account_number", "source_account")
    current_source = _snapshot_value(current_snapshot, "source_account_number", "source_account")
    if previous_source is not None and _digits_changed(previous_source, current_source):
        changed.append("source_account")

    if not changed:
        return None
    if "plan" in changed:
        plan_name = str(current_snapshot.get("plan_name") or "").strip()
        amount = _formatted_amount(current_snapshot.get("amount"))
        if plan_name and amount:
            return render_message(
                "data.confirmation.change.plan_to",
                locale,
                {"plan_name": plan_name, "amount": amount},
            )
    if changed == ["amount"]:
        amount = _formatted_amount(current_snapshot.get("amount"))
        if amount:
            return render_message("data.confirmation.change.amount_to", locale, {"amount": amount})
    if changed == ["target_phone"]:
        phone = str(current_snapshot.get("target_phone") or "").strip()
        if phone:
            return render_message("data.confirmation.change.phone_to", locale, {"phone": phone})
    if changed == ["network"]:
        network = format_network_display_name(current_snapshot.get("network"))
        if network:
            return render_message("data.confirmation.change.network_to", locale, {"network": network})
    if changed == ["source_account"]:
        source = _source_label(current_snapshot)
        if source:
            return render_message("data.confirmation.change.source_to", locale, {"source": source})
    return render_message("data.confirmation.change.details", locale)


def _source_label(snapshot: dict[str, Any]) -> str:
    bank = str(snapshot.get("source_bank_name") or "").strip()
    account = str(_snapshot_value(snapshot, "source_account_number", "source_account") or "").strip()
    if bank and account:
        return f"{bank} (...{account[-4:]})" if len(account) >= 4 else bank
    if bank:
        return bank
    if account:
        return f"...{account[-4:]}" if len(account) >= 4 else account
    return ""
