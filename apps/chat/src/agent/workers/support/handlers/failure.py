"""Handler for failure reason and wrong debit queries."""

from typing import Any, cast

from apps.chat.src.agent.workers.support.handlers.status_utils import resolve_transaction_status
from apps.chat.src.agent.workers.support.models import EscalationResult, SupportResponse
from shared.formatters.query_transaction_copy import format_transaction_status_reply
from shared.i18n.message_keys import MessageKey
from shared.i18n.renderer import (
    message_key_exists,
    render_message,
)
from shared.services.failure_categories import classify_failure_category
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _provider_error_code(provider_response: dict[str, Any], transaction: dict[str, Any]) -> str | None:
    for key in ("provider_error_code", "response_code", "responseCode", "error_code", "code"):
        value = transaction.get(key) if key == "provider_error_code" else provider_response.get(key)
        if value is not None:
            return str(value)
    return None


def _failure_category(transaction: dict[str, Any], reason: str) -> str:
    category = transaction.get("failure_category")
    if isinstance(category, str) and category.strip():
        return category.strip()
    provider_response = transaction.get("provider_response", {})
    if not isinstance(provider_response, dict):
        provider_response = {}
    return classify_failure_category(
        message=reason or str(transaction.get("error_message") or ""),
        code=_provider_error_code(provider_response, transaction),
        context="provider",
    )


def _category_guidance(category: str, *, locale: str) -> str | None:
    normalized = category.strip().lower()
    if not normalized:
        return None
    key = f"support.failure.guidance.{normalized}"
    if not message_key_exists(key, locale):
        return None
    return render_message(cast(MessageKey, key), locale)


def _append_category_guidance(message: str, category: str, *, locale: str) -> str:
    guidance = _category_guidance(category, locale=locale)
    if not guidance:
        return message
    return f"{message}\n\n{guidance}"


def _has_unified_bank_status_overlay(transaction: dict[str, Any], status: str) -> bool:
    bank_status = str(transaction.get("bank_status") or "").strip().lower().replace("_", " ")
    return bank_status == "posted" and (bool(transaction.get("needs_review")) or status in {"pending", "processing"})


def _format_unified_status_overlay(transaction: dict[str, Any], status: str, *, locale: str) -> str:
    return format_transaction_status_reply(
        status,
        locale=locale,
        local_status=transaction.get("local_status") or status,
        bank_status=transaction.get("bank_status"),
        needs_review=bool(transaction.get("needs_review")),
    )


async def handle_failure_reason(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle transfer_failure_reason intent.
    Explains why a transfer failed using provider data.
    """
    status = resolve_transaction_status(transaction)
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")
    error = transaction.get("error_message", "")
    provider_response = transaction.get("provider_response", {})

    if _has_unified_bank_status_overlay(transaction, status):
        return SupportResponse(
            message=_format_unified_status_overlay(transaction, status, locale=locale),
            transaction_data=transaction,
        )

    # Extract provider error if available
    provider_error = (
        provider_response.get("reason", "")
        or provider_response.get("message", "")
        or provider_response.get("response_message", "")
        or provider_response.get("response_code", "")
        or provider_response.get("error_code", "")
    )

    if status == "successful":
        return SupportResponse(
            message=render_message("support.failure.success_no_failure", locale),
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "failed":
        # Build explanation from available error info
        if error:
            reason = error
        elif provider_error:
            reason = provider_error
        else:
            reason = render_message("support.failure.reason_not_provided", locale)
        category = _failure_category(transaction, reason)

        # Check if money was debited
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = render_message(
                "support.failure.debited_failed",
                locale,
                {"amount": f"{amount:,.0f}", "reason": reason},
            )
            return SupportResponse(
                message=_append_category_guidance(message, category, locale=locale),
                transaction_data=transaction,
            )
        else:
            message = render_message(
                "support.failure.not_debited_failed",
                locale,
                {"amount": f"{amount:,.0f}", "reason": reason},
            )
            return SupportResponse(
                message=_append_category_guidance(message, category, locale=locale),
                offer_retry=True,
                transaction_data=transaction,
            )

    if status in {"pending", "processing"}:
        message = render_message(
            "support.failure.marked_processing",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient, "status": status},
        )
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    if status == "reversed":
        message = render_message("support.reversal.reversed", locale, {"amount": f"{amount:,.0f}"})
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    status_message = render_message("support.status.raw_status", locale, {"status": status})
    reason_message = render_message("support.failure.reason_not_provided", locale)
    return SupportResponse(
        message=f"{status_message}\n{reason_message}",
        escalation=EscalationResult(reason="unknown_error", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )


async def handle_wrong_debit(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle wrong_debit intent.
    Debited but transfer didn't complete.
    """
    status = resolve_transaction_status(transaction)
    amount = transaction.get("amount", 0)
    provider_response = transaction.get("provider_response", {})

    if status == "successful":
        return SupportResponse(
            message=render_message("support.wrong_debit.success_debit_correct", locale),
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "failed":
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = render_message(
                "support.wrong_debit.failed_but_debited",
                locale,
                {"amount": f"{amount:,.0f}"},
            )
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )
        else:
            message = render_message("support.wrong_debit.failed_not_debited", locale)
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )

    if status in {"pending", "processing"}:
        message = render_message("support.wrong_debit.pending_processing", locale)
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    return SupportResponse(
        message=render_message("support.wrong_debit.investigating", locale),
        escalation=EscalationResult(reason="wrong_debit", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )
