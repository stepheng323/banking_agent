"""Repository construction for the chat runtime."""

from dataclasses import dataclass

from banking.persistence.session_scoped import (
    SessionFactory,
    SessionScopedAccountRepository,
    SessionScopedActionableMessageRepository,
    SessionScopedBankTransactionRepository,
    SessionScopedBeneficiaryRepository,
    SessionScopedTransactionRepository,
    SessionScopedUserRepository,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatRuntimeRepositories:
    """Session-scoped repositories used by one chat runtime bundle."""

    user: SessionScopedUserRepository
    beneficiary: SessionScopedBeneficiaryRepository
    account: SessionScopedAccountRepository
    actionable_message: SessionScopedActionableMessageRepository
    bank_transaction: SessionScopedBankTransactionRepository
    transaction: SessionScopedTransactionRepository


def build_chat_runtime_repositories(session_factory: SessionFactory) -> ChatRuntimeRepositories:
    """Build chat runtime repositories with short-lived DB access."""
    logger.info("chat_worker_runtime_db_access_mode", mode="session_scoped")
    return ChatRuntimeRepositories(
        user=SessionScopedUserRepository(session_factory),
        beneficiary=SessionScopedBeneficiaryRepository(session_factory),
        account=SessionScopedAccountRepository(session_factory),
        actionable_message=SessionScopedActionableMessageRepository(session_factory),
        bank_transaction=SessionScopedBankTransactionRepository(session_factory),
        transaction=SessionScopedTransactionRepository(session_factory),
    )
