"""Repository for append-only ledger entries and lines."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import LedgerEntry, LedgerLine
from shared.money import require_naira


@dataclass(frozen=True, slots=True)
class LedgerLineInput:
    """Input for a ledger line insert."""

    account_id: UUID | str
    direction: str
    amount_naira: Decimal


class LedgerEntryRepository:
    """Append-only repository for ledger entries and lines."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_key(self, entry_key: str) -> LedgerEntry | None:
        """Return ledger entry by deterministic key."""
        result = await self.db.execute(select(LedgerEntry).filter(LedgerEntry.entry_key == entry_key))
        return result.scalars().first()

    async def get_by_keys(self, entry_keys: Sequence[str]) -> dict[str, LedgerEntry]:
        """Return existing ledger entries keyed by entry key."""
        if not entry_keys:
            return {}
        result = await self.db.execute(select(LedgerEntry).filter(LedgerEntry.entry_key.in_(entry_keys)))
        return {entry.entry_key: entry for entry in result.scalars().all()}

    async def create_entry_with_lines(
        self,
        *,
        entry_key: str,
        entry_type: str,
        amount_naira: Decimal,
        currency: str,
        lines: Sequence[LedgerLineInput],
        transaction_id: UUID | str | None = None,
        funded_transfer_id: UUID | str | None = None,
        funding_step_id: UUID | str | None = None,
        provider: str | None = None,
        provider_reference: str | None = None,
        provider_event_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        metadata: dict | None = None,
    ) -> LedgerEntry:
        """Insert an entry and all balanced lines."""
        amount = require_naira(amount_naira)
        entry = LedgerEntry(
            entry_key=entry_key,
            entry_type=entry_type,
            amount_naira=amount,
            currency=currency,
            transaction_id=transaction_id,
            funded_transfer_id=funded_transfer_id,
            funding_step_id=funding_step_id,
            provider=provider,
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type=source_type,
            source_id=source_id,
            entry_metadata=metadata or {},
        )
        self.db.add(entry)
        await self.db.flush()

        for line_number, line in enumerate(lines, start=1):
            ledger_line = LedgerLine(
                entry_id=entry.id,
                account_id=line.account_id,
                line_number=line_number,
                direction=line.direction,
                amount_naira=require_naira(line.amount_naira),
                currency=currency,
                transaction_id=transaction_id,
                funded_transfer_id=funded_transfer_id,
                funding_step_id=funding_step_id,
                provider=provider,
                provider_reference=provider_reference,
                provider_event_id=provider_event_id,
            )
            self.db.add(ledger_line)

        await self.db.flush()
        return entry

    async def liability_balance_for_transfer(self, *, liability_account_id: UUID) -> Decimal:
        """Return credit minus debit balance for one funded-transfer liability account."""
        return await self.liability_balance_for_account(liability_account_id=liability_account_id)

    async def liability_balance_for_transaction(self, *, liability_account_id: UUID) -> Decimal:
        """Return credit minus debit balance for one transaction liability account."""
        return await self.liability_balance_for_account(liability_account_id=liability_account_id)

    async def liability_balance_for_account(self, *, liability_account_id: UUID) -> Decimal:
        """Return credit minus debit balance for one liability account."""
        debit_amount = case(
            (LedgerLine.direction == "debit", LedgerLine.amount_naira),
            else_=Decimal("0.00"),
        )
        credit_amount = case(
            (LedgerLine.direction == "credit", LedgerLine.amount_naira),
            else_=Decimal("0.00"),
        )
        result = await self.db.execute(
            select(func.coalesce(func.sum(credit_amount - debit_amount), Decimal("0.00"))).filter(
                LedgerLine.account_id == liability_account_id
            )
        )
        return require_naira(result.scalar() or Decimal("0.00"))
