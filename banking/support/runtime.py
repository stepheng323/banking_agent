"""Runtime factory for support-domain workers."""

from typing import Any

from banking.support.services.ticket_service import TicketService
from banking.support.worker import SupportWorker


def build_support_worker(
    *,
    llm: Any,
    transaction_repo: Any,
    actionable_message_repo: Any,
    redis_client: Any,
    ticket_service: TicketService | None = None,
    bank_transaction_repo: Any | None = None,
) -> SupportWorker:
    """Build the support worker through the support domain boundary."""
    return SupportWorker(
        llm=llm,
        transaction_repo=transaction_repo,
        actionable_message_repo=actionable_message_repo,
        bank_transaction_repo=bank_transaction_repo,
        redis_client=redis_client,
        ticket_service=ticket_service,
    )
