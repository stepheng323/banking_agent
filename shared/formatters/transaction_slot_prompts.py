"""Contextual transaction slot prompt formatting."""

from __future__ import annotations

from typing import Any

from shared.formatters.recipient_prompt_names import sanitize_recipient_display_name
from shared.formatters.transaction_amounts import format_amount
from shared.i18n.renderer import render_message
from shared.utils.network_utils import format_network_display_name


def _payload_get(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _formatted_payload_amount(payload: Any) -> str | None:
    raw_amount = _payload_get(payload, "amount")
    if raw_amount in (None, ""):
        return None
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return format_amount(amount)


def _clean_payload_text(payload: Any, key: str) -> str | None:
    value = str(_payload_get(payload, key) or "").strip()
    return value or None


def _display_network(value: str | None) -> str | None:
    return format_network_display_name(value) or None


def _specific_transfer_recipient(payload: Any, locale: str) -> str | None:
    recipient = _clean_payload_text(payload, "recipient_resolved_name") or _clean_payload_text(
        payload, "recipient_name"
    )
    if not recipient:
        return None
    display = sanitize_recipient_display_name(recipient, locale)
    fallback = render_message("response.common.recipient_fallback", locale)
    if display == fallback:
        return None
    return display


def _format_transfer_slot_prompt(
    *,
    payload: Any,
    missing_fields: set[str],
    fallback_prompt: str,
    locale: str,
) -> str:
    amount = _formatted_payload_amount(payload)
    recipient = _specific_transfer_recipient(payload, locale)
    recipient_account = _clean_payload_text(payload, "recipient_account")
    needs_amount = missing_fields == {"amount"}
    needs_account = "recipient_account" in missing_fields
    needs_bank = "recipient_bank_name" in missing_fields

    if needs_amount and recipient:
        return render_message(
            "transaction_slots.transfer.amount_for_recipient",
            locale,
            {"recipient_name": recipient},
        )

    if needs_bank and not needs_account and recipient_account:
        return render_message(
            "transaction_slots.transfer.bank_for_account",
            locale,
            {"account": recipient_account},
        )

    if needs_account or needs_bank:
        if amount and recipient and needs_account and needs_bank:
            return render_message(
                "transaction_slots.transfer.account_bank_for_amount_recipient",
                locale,
                {"amount": amount, "recipient_name": recipient},
            )
        if amount and not recipient and needs_account and needs_bank:
            return render_message(
                "transaction_slots.transfer.recipient_for_amount",
                locale,
                {"amount": amount},
            )
        if recipient and needs_account and needs_bank:
            return render_message(
                "transaction_slots.transfer.account_bank_for_recipient",
                locale,
                {"recipient_name": recipient},
            )

    return fallback_prompt


def _data_request_label(payload: Any, locale: str) -> str | None:
    network = _display_network(_clean_payload_text(payload, "network"))
    amount = _formatted_payload_amount(payload)
    if amount and network:
        return render_message(
            "transaction_slots.data.request_amount_network",
            locale,
            {"amount": amount, "network": network},
        )
    if network:
        return render_message("transaction_slots.data.request_network", locale, {"network": network})
    if amount:
        return render_message("transaction_slots.data.request_amount", locale, {"amount": amount})
    return None


def _airtime_request_label(payload: Any, locale: str) -> str | None:
    network = _display_network(_clean_payload_text(payload, "network"))
    amount = _formatted_payload_amount(payload)
    if amount and network:
        return render_message(
            "transaction_slots.airtime.request_amount_network",
            locale,
            {"amount": amount, "network": network},
        )
    if network:
        return render_message("transaction_slots.airtime.request_network", locale, {"network": network})
    if amount:
        return render_message("transaction_slots.airtime.request_amount", locale, {"amount": amount})
    return None


def _format_airtime_slot_prompt(
    *,
    payload: Any,
    missing_fields: set[str],
    fallback_prompt: str,
    locale: str,
) -> str:
    recipient_phone = _clean_payload_text(payload, "recipient_phone") or _clean_payload_text(payload, "phone")
    network = _display_network(_clean_payload_text(payload, "network"))
    is_self = bool(_payload_get(payload, "is_self"))
    amount = _formatted_payload_amount(payload)

    if missing_fields == {"recipient_phone", "amount"} or missing_fields == {"phone", "amount"}:
        if network:
            return render_message(
                "transaction_slots.airtime.phone_amount_for_network",
                locale,
                {"network": network},
            )
        return render_message("transaction_slots.airtime.phone_amount", locale)

    if missing_fields == {"amount"} and is_self:
        if network:
            return render_message(
                "transaction_slots.airtime.amount_for_self_network",
                locale,
                {"network": network},
            )
        return render_message("transaction_slots.airtime.amount_for_self", locale)

    if missing_fields == {"amount"} and recipient_phone:
        return render_message(
            "transaction_slots.airtime.amount_for_phone",
            locale,
            {"recipient_phone": recipient_phone},
        )

    if missing_fields & {"recipient_phone", "phone"}:
        request = _airtime_request_label(payload, locale)
        if request:
            if network:
                return render_message(
                    "transaction_slots.airtime.phone_for_network_request",
                    locale,
                    {"request": request, "network": network},
                )
            return render_message(
                "transaction_slots.airtime.phone_for_request",
                locale,
                {"request": request},
            )

    if "network" in missing_fields and recipient_phone:
        if amount:
            return render_message(
                "transaction_slots.airtime.network_for_amount_phone",
                locale,
                {"amount": amount, "recipient_phone": recipient_phone},
            )
        return render_message(
            "transaction_slots.airtime.network_for_phone",
            locale,
            {"recipient_phone": recipient_phone},
        )

    return fallback_prompt


def _format_data_slot_prompt(
    *,
    payload: Any,
    missing_fields: set[str],
    fallback_prompt: str,
    locale: str,
) -> str:
    target_phone = _clean_payload_text(payload, "target_phone") or _clean_payload_text(payload, "recipient_phone")
    if "data_plan_preference" in missing_fields:
        return fallback_prompt
    if missing_fields & {"target_phone", "recipient_phone", "phone"}:
        if _clean_payload_text(payload, "plan_code") and _clean_payload_text(payload, "plan_name"):
            return fallback_prompt
        request = _data_request_label(payload, locale)
        if request:
            network = _display_network(_clean_payload_text(payload, "network"))
            if network:
                return render_message(
                    "transaction_slots.data.target_phone_for_network_request",
                    locale,
                    {"request": request, "network": network},
                )
            return render_message(
                "transaction_slots.data.target_phone_for_request",
                locale,
                {"request": request},
            )

    if "network" in missing_fields and target_phone:
        return render_message(
            "transaction_slots.data.network_for_phone",
            locale,
            {"target_phone": target_phone},
        )

    return fallback_prompt


def format_transaction_slot_prompt(
    *,
    task_type: str,
    payload: Any,
    missing_fields: list[str],
    fallback_prompt: str,
    locale: str = "en",
) -> str:
    """Render contextual slot prompts for transaction input interrupts."""
    normalized_fields = {field for field in missing_fields if isinstance(field, str)}
    if not normalized_fields:
        return fallback_prompt
    if task_type == "transfer":
        return _format_transfer_slot_prompt(
            payload=payload,
            missing_fields=normalized_fields,
            fallback_prompt=fallback_prompt,
            locale=locale,
        )
    if task_type == "data":
        return _format_data_slot_prompt(
            payload=payload,
            missing_fields=normalized_fields,
            fallback_prompt=fallback_prompt,
            locale=locale,
        )
    if task_type == "airtime":
        return _format_airtime_slot_prompt(
            payload=payload,
            missing_fields=normalized_fields,
            fallback_prompt=fallback_prompt,
            locale=locale,
        )
    return fallback_prompt
