"""Repository for transfer risk decisions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import RiskDecision


class RiskDecisionRepository(BaseRepository[RiskDecision]):
    """Stores pre-debit risk decisions by transaction idempotency key."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, RiskDecision)

    async def get_by_idempotency_key(self, idempotency_key: str) -> RiskDecision | None:
        result = await self.db.execute(select(RiskDecision).filter(RiskDecision.idempotency_key == idempotency_key))
        return result.scalars().first()

    async def record(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        decision: str,
        score: int,
        reason_codes: list[str],
        metadata: dict,
    ) -> RiskDecision:
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing:
            existing.decision = decision
            existing.score = score
            existing.reason_codes = reason_codes
            existing.risk_metadata = metadata
            self.db.add(existing)
            await self.db.flush()
            return existing
        return await self.create(
            user_id=user_id,
            idempotency_key=idempotency_key,
            decision=decision,
            score=score,
            reason_codes=reason_codes,
            risk_metadata=metadata,
        )
