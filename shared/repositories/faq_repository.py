"""Repository for FAQ entries with hybrid search capabilities."""

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from shared.database.models import FAQEntry
from shared.repositories.base import BaseRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FAQRepository(BaseRepository[FAQEntry]):
    """Repository for FAQ entries with hybrid keyword + semantic search."""

    def __init__(self, db: Session):
        super().__init__(db, FAQEntry)

    def get_all_active(self) -> list[FAQEntry]:
        """Get all active FAQ entries."""
        return self.db.query(FAQEntry).filter(FAQEntry.is_active == True).all()  # noqa: E712

    def get_by_category(self, category: str, limit: int = 10) -> list[FAQEntry]:
        """Get active FAQ entries by category."""
        return (
            self.db.query(FAQEntry)
            .filter(FAQEntry.is_active == True, FAQEntry.category == category)  # noqa: E712
            .order_by(FAQEntry.priority.desc())
            .limit(limit)
            .all()
        )

    def search_by_keywords(
        self,
        keywords: list[str],
        limit: int = 5,
        category: str | None = None,
    ) -> list[tuple[FAQEntry, int]]:
        """
        Search FAQ entries by keywords.

        Returns list of (FAQEntry, score) tuples ordered by relevance.
        Scoring:
        - Exact keyword in keywords[]: +3 points
        - Exact keyword in tags[]: +2 points
        - Keyword in question (fuzzy): +1 point
        - Add priority value
        """
        if not keywords:
            return []

        query = self.db.query(FAQEntry).filter(FAQEntry.is_active == True)  # noqa: E712

        if category:
            query = query.filter(FAQEntry.category == category)

        # Get all active entries (we'll score in Python for flexibility)
        entries = query.all()

        scored_results: list[tuple[FAQEntry, int]] = []

        for entry in entries:
            score = entry.priority  # Start with priority

            for keyword in keywords:
                keyword_lower = keyword.lower()

                # Check keywords array (highest value)
                if entry.keywords and keyword_lower in [k.lower() for k in entry.keywords]:
                    score += 3

                # Check tags array
                if entry.tags and keyword_lower in [t.lower() for t in entry.tags]:
                    score += 2

                # Check if keyword appears in question (fuzzy match)
                if keyword_lower in entry.question.lower():
                    score += 1

            if score > entry.priority:  # Only include if we found any matches
                scored_results.append((entry, score))

        # Sort by score descending
        scored_results.sort(key=lambda x: x[1], reverse=True)

        return scored_results[:limit]

    def search_fuzzy(
        self,
        query_text: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[FAQEntry]:
        """
        Fuzzy search on question text using ILIKE.

        Fallback when keyword search returns too few results.
        """
        base_query = self.db.query(FAQEntry).filter(FAQEntry.is_active == True)  # noqa: E712

        if category:
            base_query = base_query.filter(FAQEntry.category == category)

        # Search for query text in question or answer
        search_pattern = f"%{query_text}%"
        results = (
            base_query.filter(
                or_(
                    FAQEntry.question.ilike(search_pattern),
                    FAQEntry.answer.ilike(search_pattern),
                )
            )
            .order_by(FAQEntry.priority.desc())
            .limit(limit)
            .all()
        )

        return results

    def search_by_embedding(
        self,
        embedding: list[float],
        limit: int = 5,
        threshold: float = 0.7,
    ) -> list[tuple[FAQEntry, float]]:
        """
        Search FAQ entries by embedding similarity.

        NOTE: This is a placeholder for pgvector integration.
        Currently uses cosine similarity computed in Python.
        For production, use pgvector's <=> operator for efficiency.
        """
        # Get all entries with embeddings
        entries = (
            self.db.query(FAQEntry)
            .filter(FAQEntry.is_active == True, FAQEntry.embedding.isnot(None))  # noqa: E712
            .all()
        )

        if not entries:
            return []

        results: list[tuple[FAQEntry, float]] = []

        for entry in entries:
            if entry.embedding:
                similarity = self._cosine_similarity(embedding, entry.embedding)
                if similarity >= threshold:
                    results.append((entry, similarity))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    def bulk_create(self, entries: list[dict[str, Any]]) -> list[FAQEntry]:
        """Bulk create FAQ entries."""
        faq_entries = [FAQEntry(**entry) for entry in entries]
        self.db.add_all(faq_entries)
        self.db.flush()
        return faq_entries

    def update_embedding(self, faq_id: str, embedding: list[float]) -> FAQEntry | None:
        """Update the embedding for a specific FAQ entry."""
        entry = self.get_by_id(faq_id)
        if entry:
            entry.embedding = embedding
            self.db.flush()
        return entry
