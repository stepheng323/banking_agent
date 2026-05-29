"""Support intent handler dispatch."""

from typing import Any

from apps.chat.src.agent.workers.support.handlers.failure import handle_failure_reason, handle_wrong_debit
from apps.chat.src.agent.workers.support.handlers.fraud import handle_fraud
from apps.chat.src.agent.workers.support.handlers.receipt import handle_receipt_request
from apps.chat.src.agent.workers.support.handlers.retry import handle_retry
from apps.chat.src.agent.workers.support.handlers.reversal import handle_reversal_status
from apps.chat.src.agent.workers.support.handlers.status import handle_pending, handle_transfer_status
from apps.chat.src.agent.workers.support.handlers.ticket_status import handle_ticket_status
from apps.chat.src.agent.workers.support.models import SupportIntent, SupportResponse
from shared.i18n.renderer import render_message
from banking.support.services.ticket_service import TicketService


class SupportHandlerDispatcher:
    """Dispatches resolved support intents to their concrete handlers."""

    def __init__(self, *, context_manager: Any, ticket_service: TicketService | None) -> None:
        self._context_manager = context_manager
        self._ticket_service = ticket_service

    async def dispatch(
        self,
        intent: SupportIntent,
        transaction: Any,
        *,
        user_id: str,
        locale: str,
        ticket_code: str | None = None,
    ) -> SupportResponse:
        if intent == SupportIntent.TICKET_STATUS:
            ctx = await self._context_manager.get(user_id)
            if not self._ticket_service:
                return SupportResponse(message=render_message("support.unavailable", locale))
            return await handle_ticket_status(
                user_id=user_id,
                ticket_service=self._ticket_service,
                ticket_code=ticket_code,
                last_ticket_id=ctx.last_ticket_id,
                locale=locale,
            )

        if intent == SupportIntent.TRANSFER_STATUS:
            return await handle_transfer_status(transaction, locale=locale)
        if intent == SupportIntent.PENDING_TRANSFER:
            return await handle_pending(transaction, locale=locale)
        if intent == SupportIntent.FAILED_TRANSFER:
            return await handle_failure_reason(transaction, locale=locale)
        if intent == SupportIntent.WRONG_DEBIT:
            return await handle_wrong_debit(transaction, locale=locale)
        if intent in {SupportIntent.REVERSAL_REFUND, SupportIntent.WRONG_RECIPIENT}:
            return await handle_reversal_status(transaction, locale=locale)
        if intent == SupportIntent.RETRY_TRANSFER:
            return await handle_retry(transaction, locale=locale)
        if intent == SupportIntent.FRAUD_REPORT:
            return await handle_fraud(transaction, locale=locale)
        if intent == SupportIntent.RECEIPT_REQUEST:
            return await handle_receipt_request(transaction, locale=locale)
        if intent == SupportIntent.GENERAL_TX_ISSUE:
            return await handle_transfer_status(transaction, locale=locale)
        return SupportResponse(message=render_message("support.not_sure", locale))


__all__ = ["SupportHandlerDispatcher"]
