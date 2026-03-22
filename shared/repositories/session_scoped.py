"""Session-scoped repository wrappers for long-lived runtimes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.support_ticket_repository import SupportTicketRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.user_repository import UserRepository

RepoT = TypeVar("RepoT")
SessionFactory = Callable[[], AsyncSession]


class _SessionScopedRepositoryMixin:
    """Open a fresh DB session for each repository method call."""

    _session_factory: SessionFactory

    def _init_session_scoped(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def _call_with_session(
        self,
        repo_cls: type[RepoT],
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        db_session = self._session_factory()
        repo = repo_cls(db_session)
        try:
            method = getattr(repo, method_name)
            result = await method(*args, **kwargs)
            await db_session.commit()
            return result
        except Exception:
            await db_session.rollback()
            raise
        finally:
            await db_session.close()


class SessionScopedUserRepository(_SessionScopedRepositoryMixin, UserRepository):
    """User repository that never holds a session beyond one method call."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_phone(self, phone_number: str):
        return await self._call_with_session(UserRepository, "get_by_phone", phone_number)

    async def get_by_channel_identity(self, channel: str, channel_user_id: str):
        return await self._call_with_session(UserRepository, "get_by_channel_identity", channel, channel_user_id)

    async def get_by_id(self, record_id: str):
        return await self._call_with_session(UserRepository, "get_by_id", record_id)

    async def get_accounts_by_phone(self, phone_number: str):
        return await self._call_with_session(UserRepository, "get_accounts_by_phone", phone_number)


class SessionScopedBeneficiaryRepository(_SessionScopedRepositoryMixin, BeneficiaryRepository):
    """Beneficiary repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None):
        return await self._call_with_session(BeneficiaryRepository, "get_by_user", user_id, beneficiary_type)

    async def get_by_name(self, user_id: str, name: str, beneficiary_type: str | None = None):
        return await self._call_with_session(BeneficiaryRepository, "get_by_name", user_id, name, beneficiary_type)

    async def search_by_name(self, user_id: str, search_term: str, beneficiary_type: str | None = None):
        return await self._call_with_session(
            BeneficiaryRepository, "search_by_name", user_id, search_term, beneficiary_type
        )

    async def get_all_for_user(self, user_id: str):
        return await self._call_with_session(BeneficiaryRepository, "get_all_for_user", user_id)

    async def should_suggest_beneficiary(
        self,
        user_id: str,
        account_number: str,
        bank_code: str,
        beneficiary_type: str = "transfer",
    ):
        return await self._call_with_session(
            BeneficiaryRepository,
            "should_suggest_beneficiary",
            user_id,
            account_number,
            bank_code,
            beneficiary_type,
        )

    async def should_suggest_airtime_beneficiary(self, user_id: str, phone_number: str, network: str):
        return await self._call_with_session(
            BeneficiaryRepository,
            "should_suggest_airtime_beneficiary",
            user_id,
            phone_number,
            network,
        )


class SessionScopedAccountRepository(_SessionScopedRepositoryMixin, AccountRepository):
    """Account repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_user(self, user_id: str):
        return await self._call_with_session(AccountRepository, "get_by_user", user_id)

    async def get_by_id(self, record_id: str):
        return await self._call_with_session(AccountRepository, "get_by_id", record_id)

    async def get_by_account_id(self, account_id: str):
        return await self._call_with_session(AccountRepository, "get_by_account_id", account_id)

    async def get_default_account(self, user_id: str):
        return await self._call_with_session(AccountRepository, "get_default_account", user_id)

    async def set_default_account(self, user_id: str, account_id: str):
        return await self._call_with_session(AccountRepository, "set_default_account", user_id, account_id)

    async def delete_account(self, account_id: str, user_id: str):
        return await self._call_with_session(AccountRepository, "delete_account", account_id, user_id)

    async def update_mandate_status(self, mandate_id: str, status: str):
        return await self._call_with_session(AccountRepository, "update_mandate_status", mandate_id, status)


class SessionScopedActionableMessageRepository(_SessionScopedRepositoryMixin, ActionableMessageRepository):
    """Actionable message repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_user(self, user_id: str):
        return await self._call_with_session(ActionableMessageRepository, "get_by_user", user_id)

    async def get_by_channel_message_id_for_user(self, channel_message_id: str, user_id: str):
        return await self._call_with_session(
            ActionableMessageRepository,
            "get_by_channel_message_id_for_user",
            channel_message_id,
            user_id,
        )

    async def cleanup_expired(self):
        return await self._call_with_session(ActionableMessageRepository, "cleanup_expired")


class SessionScopedTransactionRepository(_SessionScopedRepositoryMixin, TransactionRepository):
    """Transaction repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_user(self, user_id: str, limit: int = 20):
        return await self._call_with_session(TransactionRepository, "get_by_user", user_id, limit)

    async def get_by_id(self, transaction_id: str):
        return await self._call_with_session(TransactionRepository, "get_by_id", transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str):
        return await self._call_with_session(TransactionRepository, "get_by_idempotency_key", idempotency_key)

    async def get_by_transaction_id(self, transaction_id: str):
        return await self._call_with_session(TransactionRepository, "get_by_transaction_id", transaction_id)

    async def get_by_status(self, user_id: str, status: str):
        return await self._call_with_session(TransactionRepository, "get_by_status", user_id, status)

    async def get_recent_unresolved(self, user_id: str, limit: int = 5):
        return await self._call_with_session(TransactionRepository, "get_recent_unresolved", user_id, limit)

    async def get_successful_transfers_since(self, user_id: str, since, limit: int = 500):
        return await self._call_with_session(
            TransactionRepository,
            "get_successful_transfers_since",
            user_id,
            since,
            limit,
        )

    async def get_recent_successful_transfer_by_recipient(self, user_id: str, recipient_name: str):
        return await self._call_with_session(
            TransactionRepository,
            "get_recent_successful_transfer_by_recipient",
            user_id,
            recipient_name,
        )

    async def update_status(self, transaction_id: str, status: str, error_message: str | None = None):
        return await self._call_with_session(
            TransactionRepository,
            "update_status",
            transaction_id,
            status,
            error_message,
        )


class SessionScopedSupportTicketRepository(_SessionScopedRepositoryMixin, SupportTicketRepository):
    """Support ticket repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def get_by_ticket_code(self, ticket_code: str):
        return await self._call_with_session(SupportTicketRepository, "get_by_ticket_code", ticket_code)

    async def get_open_tickets(self, user_id: str):
        return await self._call_with_session(SupportTicketRepository, "get_open_tickets", user_id)

    async def get_latest_open(self, user_id: str):
        return await self._call_with_session(SupportTicketRepository, "get_latest_open", user_id)

    async def get_by_user(self, user_id: str, limit: int = 20, include_closed: bool = False):
        return await self._call_with_session(
            SupportTicketRepository,
            "get_by_user",
            user_id,
            limit,
            include_closed,
        )
