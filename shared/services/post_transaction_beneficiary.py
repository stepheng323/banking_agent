"""Helpers for post-transaction beneficiary suggestions."""

from __future__ import annotations

from typing import Any, Protocol


class BeneficiarySuggestionServiceProtocol(Protocol):
    async def check_and_suggest_beneficiary(
        self,
        *,
        phone_number: str,
        beneficiary_type: str,
        recipient_data: dict[str, Any],
        transaction_id: str | None = None,
        send_message: bool = False,
        channel: str = "whatsapp",
        locale: str = "en",
    ) -> str | None: ...


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _digits(value: Any) -> str:
    return "".join(ch for ch in _clean_text(value) if ch.isdigit())


def _same_local_phone(left: Any, right: Any) -> bool:
    left_digits = _digits(left)
    right_digits = _digits(right)
    if not left_digits or not right_digits:
        return False
    if left_digits == right_digits:
        return True
    return len(left_digits) >= 10 and len(right_digits) >= 10 and left_digits[-10:] == right_digits[-10:]


def append_beneficiary_suggestion(text: str, suggestion: str | None) -> str:
    suggestion_text = _clean_text(suggestion)
    if not suggestion_text:
        return text
    base = _clean_text(text)
    return f"{base}\n\n{suggestion_text}" if base else suggestion_text


async def suggest_transfer_beneficiary(
    suggestion_service: BeneficiarySuggestionServiceProtocol | None,
    *,
    phone_number: str,
    channel: str,
    locale: str,
    transaction_id: str | None,
    account_number: Any,
    bank_code: Any,
    bank_name: Any,
    recipient_name: Any,
    original_alias: Any = None,
    bank_code_provider: Any = None,
    resolution_provider: Any = None,
    is_self: Any = False,
) -> str | None:
    if suggestion_service is None:
        return None
    if bool(is_self):
        return None

    cleaned_account_number = _clean_text(account_number)
    cleaned_bank_code = _clean_text(bank_code)
    cleaned_bank_name = _clean_text(bank_name)
    if not cleaned_account_number or not (cleaned_bank_code or cleaned_bank_name):
        return None

    return await suggestion_service.check_and_suggest_beneficiary(
        phone_number=phone_number,
        beneficiary_type="transfer",
        recipient_data={
            "account_number": cleaned_account_number,
            "bank_code": cleaned_bank_code,
            "bank_name": cleaned_bank_name,
            "recipient_bank_code_provider": _clean_text(bank_code_provider),
            "recipient_resolution_provider": _clean_text(resolution_provider),
            "name": _clean_text(recipient_name),
            "original_alias": _clean_text(original_alias),
            "is_self": bool(is_self),
        },
        transaction_id=transaction_id,
        send_message=False,
        channel=channel,
        locale=locale,
    )


async def suggest_mobile_beneficiary(
    suggestion_service: BeneficiarySuggestionServiceProtocol | None,
    *,
    phone_number: str,
    channel: str,
    locale: str,
    transaction_id: str | None,
    beneficiary_type: str,
    recipient_phone: Any,
    network: Any,
    recipient_name: Any = None,
) -> str | None:
    if suggestion_service is None:
        return None
    if beneficiary_type not in {"airtime", "data"}:
        return None

    cleaned_recipient_phone = _clean_text(recipient_phone)
    cleaned_network = _clean_text(network)
    if not phone_number or not cleaned_recipient_phone or not cleaned_network:
        return None
    if _same_local_phone(phone_number, cleaned_recipient_phone):
        return None

    return await suggestion_service.check_and_suggest_beneficiary(
        phone_number=phone_number,
        beneficiary_type=beneficiary_type,
        recipient_data={
            "phone": cleaned_recipient_phone,
            "network": cleaned_network,
            "name": _clean_text(recipient_name),
        },
        transaction_id=transaction_id,
        send_message=False,
        channel=channel,
        locale=locale,
    )
