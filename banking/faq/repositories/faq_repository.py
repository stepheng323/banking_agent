from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import FAQEntry
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FAQRepository(BaseRepository[FAQEntry]):
    """Repository for FAQ entries with hybrid keyword + semantic search."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, FAQEntry)

    async def get_all_active(self) -> list[FAQEntry]:
        """Get all active FAQ entries."""
        result = await self.db.execute(select(FAQEntry).filter(FAQEntry.is_active == True))  # noqa: E712
        return list(result.scalars().all())

    async def get_by_category(self, category: str, limit: int = 10) -> list[FAQEntry]:
        """Get active FAQ entries by category."""
        result = await self.db.execute(
            select(FAQEntry)
            .filter(FAQEntry.is_active == True, FAQEntry.category == category)  # noqa: E712
            .order_by(FAQEntry.priority.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def search_by_keywords(
        self,
        keywords: list[str],
        limit: int = 5,
        category: str | None = None,
    ) -> list[tuple[FAQEntry, int]]:
        """
        Search FAQ entries by keywords.

        Returns list of (FAQEntry, score) tuples ordered by relevance.
        """
        if not keywords:
            return []

        stmt = select(FAQEntry).filter(FAQEntry.is_active == True)  # noqa: E712
        if category:
            stmt = stmt.filter(FAQEntry.category == category)

        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        scored_results: list[tuple[FAQEntry, int]] = []

        for entry in entries:
            score = float(entry.priority)

            for keyword in keywords:
                keyword_lower = keyword.lower()

                entry_keywords: list[str] = list(entry.keywords) if entry.keywords is not None else []
                if keyword_lower in [str(k).lower() for k in entry_keywords]:
                    score += 3.0

                entry_tags: list[str] = list(entry.tags) if entry.tags is not None else []
                if keyword_lower in [str(t).lower() for t in entry_tags]:
                    score += 2.0

                if keyword_lower in str(entry.question).lower():
                    score += 1.0

            if score > float(entry.priority):
                scored_results.append((entry, int(score)))

        scored_results.sort(key=lambda x: x[1], reverse=True)

        return scored_results[:limit]

    async def search_fuzzy(
        self,
        query_text: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[FAQEntry]:
        """Fuzzy search on question text using ILIKE."""
        stmt = select(FAQEntry).filter(FAQEntry.is_active == True)  # noqa: E712

        if category:
            stmt = stmt.filter(FAQEntry.category == category)

        search_pattern = f"%{query_text}%"
        stmt = (
            stmt.filter(
                or_(
                    FAQEntry.question.ilike(search_pattern),
                    FAQEntry.answer.ilike(search_pattern),
                )
            )
            .order_by(FAQEntry.priority.desc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def search_by_embedding(
        self,
        embedding: list[float],
        limit: int = 5,
        threshold: float = 0.7,
    ) -> list[tuple[FAQEntry, float]]:
        """Search FAQ entries by embedding similarity."""
        result = await self.db.execute(
            select(FAQEntry).filter(FAQEntry.is_active == True, FAQEntry.embedding.isnot(None))  # noqa: E712
        )
        entries = result.scalars().all()

        if not entries:
            return []

        results: list[tuple[FAQEntry, float]] = []

        for entry in entries:
            if entry.embedding is not None:
                similarity = self._cosine_similarity(embedding, cast(list[float], entry.embedding))
                if similarity >= threshold:
                    results.append((entry, similarity))

        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=True))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    async def bulk_create(self, entries: list[dict[str, Any]]) -> list[FAQEntry]:
        """Bulk create FAQ entries."""
        faq_entries = [FAQEntry(**entry) for entry in entries]
        self.db.add_all(faq_entries)
        await self.db.flush()
        return faq_entries

    async def update_embedding(self, faq_id: str, embedding: list[float]) -> FAQEntry | None:
        """Update the embedding for a specific FAQ entry."""
        entry = await self.get_by_id(faq_id)
        if entry:
            entry.embedding = embedding
            await self.db.flush()
        return entry
