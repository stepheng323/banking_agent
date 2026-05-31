"""Repository for ledger accounts."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import LedgerAccount


class LedgerAccountRepository:
    """Append-safe account repository for double-entry ledger accounts."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_code(self, code: str) -> LedgerAccount | None:
        """Return a ledger account by stable account code."""
        result = await self.db.execute(select(LedgerAccount).filter(LedgerAccount.code == code))
        return result.scalars().first()

    async def get_or_create(
        self,
        *,
        code: str,
        name: str,
        account_type: str,
        normal_balance: str,
        currency: str = "NGN",
        owner_type: str | None = None,
        owner_id: UUID | str | None = None,
        provider: str | None = None,
        user_id: UUID | str | None = None,
    ) -> LedgerAccount:
        """Get or create a ledger account by code."""
        existing = await self.get_by_code(code)
        if existing:
            return existing

        account = LedgerAccount(
            code=code,
            name=name,
            account_type=account_type,
            normal_balance=normal_balance,
            currency=currency,
            owner_type=owner_type,
            owner_id=owner_id,
            provider=provider,
            user_id=user_id,
        )
        self.db.add(account)
        await self.db.flush()
        return account
