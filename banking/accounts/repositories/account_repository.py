"""Repository for Account model."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from banking.accounts.mandate_state import is_mandate_transition_allowed, normalize_mandate_status
from banking.persistence.base import BaseRepository
from shared.database.models import Account
from shared.models.account import CreateAccount
from shared.security.field_encryption import blind_index


class AccountRepository(BaseRepository[Account]):
    """Repository for Account operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Account)

    async def get_by_user(self, user_id: str) -> list[Account]:
        """Get all accounts for a user."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass

        result = await self.db.execute(select(Account).filter(Account.user_id == user_uuid))
        return list(result.scalars().all())

    async def get_by_user_for_update(self, user_id: str) -> list[Account]:
        """Lock all linked accounts for lifecycle mutation preflight."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        result = await self.db.execute(select(Account).filter(Account.user_id == user_uuid).with_for_update())
        return list(result.scalars().all())

    async def get_by_account_id(self, account_id: str) -> Account | None:
        """Get account by account_id (external ID)."""
        result = await self.db.execute(select(Account).filter(Account.account_id == account_id))
        return result.scalars().first()

    async def create_account(
        self,
        create_account: CreateAccount,
    ) -> Account:
        """Create a bank account."""
        account_dict = create_account.model_dump(exclude_unset=True)
        if "extra_data" not in account_dict or account_dict["extra_data"] is None:
            account_dict["extra_data"] = {}

        account = Account(**account_dict)
        self.db.add(account)
        await self.db.flush()
        return account

    async def get_default_account(self, user_id: str) -> Account | None:
        """Get user's default account."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass

        result = await self.db.execute(select(Account).filter(Account.user_id == user_uuid, Account.is_default == True))
        return result.scalars().first()

    async def set_default_account(self, user_id: str, account_id: str) -> Account:
        """
        Set an account as the default for a user.
        Unsets any existing default account.
        """
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass

        # Unset existing default
        await self.db.execute(
            update(Account).where(Account.user_id == user_uuid, Account.is_default == True).values(is_default=False)
        )

        account = await self.get_by_account_id(account_id)
        if account and str(account.user_id) == str(user_uuid):
            # Use cast to handle SQLAlchemy InstrumentedAttribute assignment
            account.is_default = cast(Any, True)
            await self.db.flush()
            return account

        raise ValueError(f"Account {account_id} not found for user {user_id}")

    async def delete_account(
        self,
        account_id: str,
        user_id: str,
        *,
        assign_new_default: bool = True,
    ) -> bool:
        """
        Delete (unlink) an account.
        Returns True if successful, False otherwise.
        """
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass

        account = await self.get_by_account_id(account_id)
        if account and str(account.user_id) == str(user_uuid):
            was_default = account.is_default

            await self.db.delete(account)
            await self.db.flush()

            if was_default and assign_new_default:
                remaining_accounts = await self.get_by_user(user_id)
                if remaining_accounts:
                    remaining_accounts[0].is_default = cast(Any, True)
                    await self.db.flush()

            return True

        return False

    async def get_by_mandate_id(self, mandate_id: str) -> Account | None:
        """Get account by Mono mandate ID."""
        mandate_lookup = blind_index("accounts.mandate_id", mandate_id)
        if not mandate_lookup:
            return None
        result = await self.db.execute(select(Account).filter(Account.mandate_id_blind_index == mandate_lookup))
        return result.scalars().first()

    async def get_by_mandate_id_for_update(self, mandate_id: str) -> Account | None:
        """Get account by Mono mandate ID with a row lock."""
        mandate_lookup = blind_index("accounts.mandate_id", mandate_id)
        if not mandate_lookup:
            return None
        result = await self.db.execute(
            select(Account).filter(Account.mandate_id_blind_index == mandate_lookup).with_for_update()
        )
        return result.scalars().first()

    async def get_mandate_id_for_provider(self, account_id: str) -> str | None:
        """Return decrypted mandate ID for provider calls only."""
        account = await self.get_by_account_id(account_id)
        return account.mandate_id if account else None

    async def update_mandate_status(self, mandate_id: str, status: str) -> Account | None:
        """Update mandate status by mandate ID. Returns updated account or None if not found."""
        account = await self.get_by_mandate_id(mandate_id)
        if account:
            account.mandate_status = cast(Any, status)
            await self.db.flush()
        return account

    async def apply_mandate_status_event(
        self,
        mandate_id: str,
        status: str,
        *,
        event_name: str,
        event_id: str | None = None,
        provider_payload: dict[str, Any] | None = None,
    ) -> tuple[Account | None, bool]:
        """Apply a Mono mandate webhook status only when the state transition is safe."""
        account = await self.get_by_mandate_id_for_update(mandate_id)
        if not account:
            return None, False

        current_status = normalize_mandate_status(account.mandate_status)
        incoming_status = normalize_mandate_status(status)
        if not is_mandate_transition_allowed(current_status, incoming_status, event_name=event_name):
            return account, False

        account.mandate_status = cast(Any, incoming_status)
        extra_data = dict(account.extra_data or {})
        payload = provider_payload or {}
        extra_data["mandate_state"] = {
            "event_name": event_name,
            "event_id": event_id,
            "provider_status": _safe_str(payload.get("status")),
            "ready_to_debit": (
                payload.get("ready_to_debit") if isinstance(payload.get("ready_to_debit"), bool) else None
            ),
            "reason": _safe_str(payload.get("reason") or payload.get("message") or payload.get("description")),
            "updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        }
        account.extra_data = extra_data
        await self.db.flush()
        return account, True


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
