"""Support output helpers and result shaping."""

import re
import uuid
from typing import Any

from banking.policy.service import capability_block_message
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import SupportOutcome, SupportResult
from banking.support.handlers.escalation import handle_escalation
from banking.support.handlers.retry import build_retry_quoted_data
from banking.support.models import SupportIntent, SupportResponse
from banking.support.services.ticket_service import TicketService
from shared.queue.models import ReceiptJobPayload, ReceiptTransferData

_TICKET_CODE_RE = re.compile(r"\b(SUP-\d{8}-\d{4})\b", re.IGNORECASE)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def build_retry_handoff(response: SupportResponse, intent: SupportIntent) -> dict[str, Any] | None:
    if intent != SupportIntent.RETRY_TRANSFER or not response.offer_retry:
        return response.handoff
    transaction = response.transaction_data
    if not isinstance(transaction, dict):
        return response.handoff
    quoted_data = build_retry_quoted_data(transaction)
    return {
        "type": "retry_transfer",
        "payload": quoted_data.get("data", {}),
        "quoted_data": quoted_data,
        "requires_confirmation": True,
    }


def result_from_support_response(
    response: SupportResponse | None,
    *,
    intent: SupportIntent,
    locale: str,
) -> SupportResult:
    final_msg = response.message if response else render_message("support.unable_to_process", locale)
    handoff = build_retry_handoff(response, intent) if response else None
    return SupportResult(
        outcome=SupportOutcome.OK,
        response=final_msg,
        final_message=final_msg,
        handoff=handoff,
    )


def build_receipt_transfer_data(transaction: dict[str, Any], *, locale: str) -> ReceiptTransferData:
    source_account_name = _optional_text(transaction.get("source_account_name"))
    return {
        "amount": transaction.get("amount"),
        "source": {
            "name": transaction.get("source_bank_name"),
            "account_name": source_account_name or render_message("query.receipt.user_account", locale),
            "account_number": transaction.get("source_account_number"),
        },
        "recipient": {
            "name": transaction.get("recipient_name"),
            "account_number": transaction.get("recipient_account_number"),
            "bank_name": transaction.get("recipient_bank_name"),
        },
        "narration": transaction.get("narration"),
        "session_id": str(transaction.get("transaction_id") or transaction.get("id") or ""),
    }


def build_receipt_job(
    *,
    transaction: dict[str, Any],
    context: dict[str, Any],
    locale: str,
) -> ReceiptJobPayload:
    phone_number = str(context.get("phone_number") or "")
    channel = str(context.get("channel") or "whatsapp")
    channel_identity = context.get("channel_identity")
    transaction_reference = str(transaction.get("transaction_id") or transaction.get("id") or "")
    return {
        "phone_number": phone_number,
        "channel": channel,
        "channel_identity": (
            str(channel_identity) if isinstance(channel_identity, str) and channel_identity.strip() else None
        ),
        "transfer_data": build_receipt_transfer_data(transaction, locale=locale),
        "transaction_reference": transaction_reference or None,
        "signal_key": f"receipt:{uuid.uuid4()}",
    }


def receipt_unavailable_result(*, transaction: dict[str, Any], locale: str) -> SupportResult:
    response = render_message(
        "support.receipt.unavailable_for_status",
        locale,
        {"status": str(transaction.get("status", "unknown") or "unknown").strip().lower()},
    )
    return SupportResult(
        outcome=SupportOutcome.OK,
        response=response,
        final_message=response,
    )


def receipt_only_transfer_result(*, transaction_type: str, locale: str) -> SupportResult:
    response = render_message(
        "query.receipt.only_transfer",
        locale,
        {"transaction_type": transaction_type.replace("_", " ") or "transaction"},
    )
    return SupportResult(
        outcome=SupportOutcome.OK,
        response=response,
        final_message=response,
    )


def build_receipt_result(
    *,
    transaction: dict[str, Any],
    context: dict[str, Any],
    locale: str,
) -> SupportResult:
    transaction_type = str(transaction.get("transaction_type") or "").strip().lower()
    if transaction_type != "transfer":
        return receipt_only_transfer_result(transaction_type=transaction_type, locale=locale)

    status = str(transaction.get("status", "unknown") or "unknown").strip().lower()
    if status not in {"success", "successful"}:
        return receipt_unavailable_result(transaction=transaction, locale=locale)

    response = render_message("query.receipt.generating", locale)
    return SupportResult(
        outcome=SupportOutcome.OK,
        response=response,
        final_message=response,
        receipt_jobs=[build_receipt_job(transaction=transaction, context=context, locale=locale)],
    )


def ticket_code_from_message(message: str) -> str | None:
    match = _TICKET_CODE_RE.search(message)
    if match is None:
        return None
    return match.group(1).upper()


async def create_ticket_response(
    *,
    user_id: str,
    intent: Any,
    transaction: Any,
    reason: str,
    context_manager: Any,
    ticket_service: TicketService | None,
    locale: str = "en",
) -> SupportResult:
    if policy_message := capability_block_message(domain="support", action="create_ticket", locale=locale):
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=policy_message,
            final_message=policy_message,
        )

    if not ticket_service:
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=render_message("support.escalation_unavailable", locale),
        )

    resp = await handle_escalation(
        user_id=user_id,
        intent=intent.value if hasattr(intent, "value") else str(intent),
        ticket_service=ticket_service,
        transaction=transaction,
        reason=reason,
        locale=locale,
    )

    ticket_code = None
    if resp.escalation and resp.escalation.context:
        ticket_code = resp.escalation.context.get("ticket_code")

    await context_manager.reset_on_resolution(
        user_id=user_id,
        ticket_id=ticket_code,
        transaction_ref=transaction.get("transaction_id") if transaction else None,
    )

    return SupportResult(
        outcome=SupportOutcome.OK,
        response=resp.message,
        final_message=resp.message,
        ticket_code=ticket_code,
        escalation=resp.escalation,
    )


__all__ = [
    "build_receipt_job",
    "build_receipt_result",
    "build_receipt_transfer_data",
    "build_retry_handoff",
    "create_ticket_response",
    "receipt_only_transfer_result",
    "receipt_unavailable_result",
    "result_from_support_response",
    "ticket_code_from_message",
]
