"""Handler for retry transfer requests."""

from typing import Any, cast

from apps.chat.src.agent.workers.support.handlers.status_utils import resolve_transaction_status
from apps.chat.src.agent.workers.support.models import SupportResponse
from banking.transactions.runtime.failure_categories import classify_failure_category
from shared.formatters.query_transaction_copy import format_transaction_status_reply
from shared.i18n.message_keys import MessageKey
from shared.i18n.renderer import (
    message_key_exists,
    render_message,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _provider_error_code(transaction: dict[str, Any]) -> str | None:
    provider_response = transaction.get("provider_response", {})
    if not isinstance(provider_response, dict):
        provider_response = {}
    for key in ("provider_error_code", "response_code", "responseCode", "error_code", "code"):
        value = transaction.get(key) if key == "provider_error_code" else provider_response.get(key)
        if value is not None:
            return str(value)
    return None


def _failure_category(transaction: dict[str, Any], error: str) -> str:
    category = transaction.get("failure_category")
    if isinstance(category, str) and category.strip():
        return category.strip()
    return classify_failure_category(
        message=error,
        code=_provider_error_code(transaction),
        context="provider",
    )


def _retry_block_message(category: str, error: str, *, locale: str) -> str:
    normalized = category.strip().lower()
    key = f"support.retry.block.{normalized}"
    if normalized and message_key_exists(key, locale):
        return render_message(cast(MessageKey, key), locale)
    return render_message("support.retry.not_retryable", locale, {"error": error})


async def handle_retry(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle retry_transfer intent.
    Checks if retryable and prepares for transfer retry hydration.
    """
    status = resolve_transaction_status(transaction)
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")
    actionable = transaction.get("actionable")

    if status == "successful":
        message = render_message(
            "support.retry.already_success",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        return SupportResponse(
            message=message,
            offer_retry=True,  # Actually means "send again"
            transaction_data=transaction,
        )

    if status in {"pending", "processing"}:
        message = render_message("support.retry.pending_wait", locale)
        return SupportResponse(
            message=message,
            offer_retry=False,
            transaction_data=transaction,
        )

    if isinstance(actionable, dict) and not actionable.get("retry", False):
        message = (
            format_transaction_status_reply(
                status,
                locale=locale,
                local_status=transaction.get("local_status") or status,
                bank_status=transaction.get("bank_status"),
                needs_review=bool(transaction.get("needs_review")),
            )
            if transaction.get("needs_review") and transaction.get("bank_status") == "posted"
            else render_message("support.retry.not_retryable", locale, {"error": status})
        )
        return SupportResponse(
            message=message,
            offer_retry=False,
            transaction_data=transaction,
        )

    if status == "failed":
        error = transaction.get("error_message", "")
        category = _failure_category(transaction, str(error or ""))
        is_retryable = category not in {"insufficient_funds", "source_account", "validation_error"}

        if is_retryable:
            message = render_message(
                "support.retry.ready",
                locale,
                {"amount": f"{amount:,.0f}", "recipient": recipient},
            )
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )
        else:
            message = _retry_block_message(category, str(error or ""), locale=locale)
            return SupportResponse(
                message=message,
                offer_retry=False,
                transaction_data=transaction,
            )

    return SupportResponse(
        message=render_message("support.retry.unknown_status", locale, {"status": status}),
        transaction_data=transaction,
    )


def build_retry_quoted_data(transaction: dict[str, Any]) -> dict[str, Any]:
    """
    Build quoted_data for hydrating a transfer retry.
    This matches the expected format from ActionableMessage.
    """
    return {
        "type": "retry_transfer",
        "data": {
            "amount": transaction.get("amount"),
            "recipient_name": transaction.get("recipient_name"),
            "recipient_account_number": transaction.get("recipient_account_number"),
            "recipient_bank_code": transaction.get("recipient_bank_code"),
            "recipient_bank_name": transaction.get("recipient_bank_name"),
            "narration": transaction.get("narration"),
            "source_bank_name": transaction.get("source_bank_name"),
        },
    }
