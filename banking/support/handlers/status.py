"""Handler for transfer status and pending queries."""

from typing import Any

from banking.presentation.formatters.query_transaction_copy import format_transaction_status_reply
from banking.presentation.formatters.support_transaction_copy import format_support_transfer_status_sentence
from banking.presentation.i18n.renderer import render_message
from banking.support.handlers.status_utils import resolve_transaction_status
from banking.support.models import SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _has_unified_bank_status_overlay(transaction: dict[str, Any], status: str) -> bool:
    bank_status = str(transaction.get("bank_status") or "").strip().lower().replace("_", " ")
    return bank_status == "posted" and (
        bool(transaction.get("needs_review")) or status in {"pending", "processing", "posted"}
    )


def _format_unified_status_overlay(transaction: dict[str, Any], status: str, *, locale: str) -> str:
    return format_transaction_status_reply(
        status,
        locale=locale,
        local_status=transaction.get("local_status") or status,
        bank_status=transaction.get("bank_status"),
        needs_review=bool(transaction.get("needs_review")),
    )


async def handle_transfer_status(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle transfer_status intent.
    Confirms success or explains current state.
    """
    status = resolve_transaction_status(transaction)

    if _has_unified_bank_status_overlay(transaction, status):
        return SupportResponse(
            message=_format_unified_status_overlay(transaction, status, locale=locale),
            transaction_data=transaction,
        )

    if status == "successful":
        return SupportResponse(
            message=format_support_transfer_status_sentence(transaction, status=status, locale=locale),
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status in {"pending", "processing"}:
        return SupportResponse(
            message=format_support_transfer_status_sentence(transaction, status=status, locale=locale),
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = format_support_transfer_status_sentence(transaction, status=status, locale=locale)
        if error:
            message = f"{message}\n{render_message('support.common.reason', locale, {'reason': error})}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=format_support_transfer_status_sentence(transaction, status=status, locale=locale),
            transaction_data=transaction,
        )


async def handle_pending(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle pending_transfer intent.
    Explains why transfer is stuck.
    """
    status = resolve_transaction_status(transaction)
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")

    if status in {"pending", "processing"}:
        message = render_message(
            "support.pending.pending",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    elif status == "successful":
        message = render_message("support.pending.success_completed", locale)
        return SupportResponse(
            message=message,
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = render_message("support.pending.failed_no_longer_pending", locale)
        if error:
            message = f"{message}\n{render_message('support.common.reason', locale, {'reason': error})}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=render_message("support.pending.raw_status", locale, {"status": status}),
            transaction_data=transaction,
        )
