"""Session-scoped repository wrappers for long-lived runtimes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.identity.repositories.user_repository import UserRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.support.repositories.support_ticket_repository import SupportTicketRepository
from banking.transactions.repositories.bank_transaction_coverage_repository import BankTransactionCoverageRepository
from banking.transactions.repositories.bank_transaction_repository import BankTransactionRepository
from banking.transactions.repositories.transaction_repository import TransactionRepository

RepoT = TypeVar("RepoT")
SessionFactory = Callable[[], AsyncSession]


class _SessionScopedRepositoryMixin:
    """Open a fresh DB session for each repository method call."""

    _session_factory: SessionFactory

    def _init_session_scoped(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def _call_with_session(
        self,
        repo_cls: Callable[[AsyncSession], RepoT],
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

    async def get_by_email(self, email: str):
        return await self._call_with_session(UserRepository, "get_by_email", email)

    async def get_by_channel_identity(self, channel: str, channel_user_id: str):
        return await self._call_with_session(UserRepository, "get_by_channel_identity", channel, channel_user_id)

    async def get_channel_identity_by_phone(self, phone_number: str, channel: str):
        return await self._call_with_session(UserRepository, "get_channel_identity_by_phone", phone_number, channel)

    async def link_channel_identity(self, user_id: str, channel: str, channel_user_id: str):
        return await self._call_with_session(UserRepository, "link_channel_identity", user_id, channel, channel_user_id)

    async def get_by_id(self, record_id: str):
        return await self._call_with_session(UserRepository, "get_by_id", record_id)

    async def is_registered(self, phone_number: str):
        return await self._call_with_session(UserRepository, "is_registered", phone_number)

    async def register_user(self, user_data):
        return await self._call_with_session(UserRepository, "register_user", user_data)

    async def update_user(self, user_id: str, user_data):
        return await self._call_with_session(UserRepository, "update_user", user_id, user_data)

    async def update_last_active(self, user_id: str):
        return await self._call_with_session(UserRepository, "update_last_active", user_id)

    async def get_registered_count(self):
        return await self._call_with_session(UserRepository, "get_registered_count")

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

    async def should_suggest_beneficiary(
        self,
        user_id: str,
        account_number: str,
        bank_code: str | None,
        bank_name: str | None = None,
        beneficiary_type: str = "transfer",
    ) -> bool:
        return cast(
            bool,
            await self._call_with_session(
                BeneficiaryRepository,
                "should_suggest_beneficiary",
                user_id,
                account_number,
                bank_code,
                bank_name,
                beneficiary_type,
            ),
        )

    async def should_suggest_mobile_beneficiary(self, user_id: str, phone_number: str, network: str):
        return await self._call_with_session(
            BeneficiaryRepository,
            "should_suggest_mobile_beneficiary",
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

    async def get_by_user_for_update(self, user_id: str):
        return await self._call_with_session(AccountRepository, "get_by_user_for_update", user_id)

    async def get_by_id(self, record_id: str):
        return await self._call_with_session(AccountRepository, "get_by_id", record_id)

    async def get_by_account_id(self, account_id: str):
        return await self._call_with_session(AccountRepository, "get_by_account_id", account_id)

    async def get_default_account(self, user_id: str):
        return await self._call_with_session(AccountRepository, "get_default_account", user_id)

    async def set_default_account(self, user_id: str, account_id: str):
        return await self._call_with_session(AccountRepository, "set_default_account", user_id, account_id)

    async def delete_account(
        self,
        account_id: str,
        user_id: str,
        *,
        assign_new_default: bool = True,
    ):
        return await self._call_with_session(
            AccountRepository,
            "delete_account",
            account_id,
            user_id,
            assign_new_default=assign_new_default,
        )

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

    async def list_by_user_window(self, user_id: str, *, start_date, end_date, limit: int = 200):
        return await self._call_with_session(
            TransactionRepository,
            "list_by_user_window",
            user_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

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

    async def get_successful_transfer_personality_stats(
        self,
        user_id: str,
        *,
        recipient_account_number: str | None = None,
        recipient_name: str | None = None,
        recipient_since: datetime | None = None,
        exclude_transaction_id: str | None = None,
        exclude_idempotency_key: str | None = None,
    ):
        return await self._call_with_session(
            TransactionRepository,
            "get_successful_transfer_personality_stats",
            user_id,
            recipient_account_number=recipient_account_number,
            recipient_name=recipient_name,
            recipient_since=recipient_since,
            exclude_transaction_id=exclude_transaction_id,
            exclude_idempotency_key=exclude_idempotency_key,
        )

    async def update_status(
        self,
        transaction_id: str,
        status: str,
        error_message: str | None = None,
        *,
        provider_transaction_id: str | None = None,
        provider_status: str | None = None,
        provider_response: dict[Any, Any] | None = None,
        provider_error_code: str | None = None,
    ):
        return await self._call_with_session(
            TransactionRepository,
            "update_status",
            transaction_id,
            status,
            error_message,
            provider_transaction_id=provider_transaction_id,
            provider_status=provider_status,
            provider_response=provider_response,
            provider_error_code=provider_error_code,
        )


class SessionScopedBankTransactionRepository(_SessionScopedRepositoryMixin, BankTransactionRepository):
    """Mirrored bank transaction repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def bulk_upsert(self, rows: list[dict]):
        return await self._call_with_session(BankTransactionRepository, "bulk_upsert", rows)

    async def list_by_account_window(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ):
        return await self._call_with_session(
            BankTransactionRepository,
            "list_by_account_window",
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
        )

    async def list_by_accounts_window(
        self,
        linked_account_ids: list[str | UUID],
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ):
        return await self._call_with_session(
            BankTransactionRepository,
            "list_by_accounts_window",
            linked_account_ids,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
        )

    async def get_latest_posted_at(self, linked_account_id: str | UUID, *, provider: str = "mono") -> datetime | None:
        return cast(
            datetime | None,
            await self._call_with_session(
                BankTransactionRepository,
                "get_latest_posted_at",
                linked_account_id,
                provider=provider,
            ),
        )


class SessionScopedBankTransactionCoverageRepository(_SessionScopedRepositoryMixin, BankTransactionCoverageRepository):
    """Coverage repository with per-call DB sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._init_session_scoped(session_factory)

    async def list_for_account(
        self,
        linked_account_id: str | UUID,
        *,
        provider: str = "mono",
        coverage_type: str = "full",
    ):
        return await self._call_with_session(
            BankTransactionCoverageRepository,
            "list_for_account",
            linked_account_id,
            provider=provider,
            coverage_type=coverage_type,
        )

    async def find_missing_gaps(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ):
        return await self._call_with_session(
            BankTransactionCoverageRepository,
            "find_missing_gaps",
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            coverage_type=coverage_type,
        )

    async def is_window_covered(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ):
        return await self._call_with_session(
            BankTransactionCoverageRepository,
            "is_window_covered",
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            coverage_type=coverage_type,
        )

    async def add_full_coverage(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ):
        return await self._call_with_session(
            BankTransactionCoverageRepository,
            "add_full_coverage",
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
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
