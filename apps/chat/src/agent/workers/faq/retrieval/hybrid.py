"""Hybrid retriever combining keyword and semantic search."""

from typing import Any

from apps.chat.src.agent.workers.faq.models import FAQRetrievalHit
from apps.chat.src.agent.workers.faq.retrieval.normalizer import QueryNormalizer
from shared.repositories.faq_repository import FAQRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class HybridRetriever:
    """Hybrid retriever using keyword + fuzzy + semantic search.

    Priority:
    1. Keyword match on tags/keywords arrays (highest confidence)
    2. Semantic match using embeddings (if available)
    3. Fuzzy match on question/answer text (fallback)
    """

    HIGH_CONFIDENCE = 0.7
    MEDIUM_CONFIDENCE = 0.4
    LOW_CONFIDENCE = 0.2

    def __init__(
        self,
        db: Any,
        normalizer: QueryNormalizer | None = None,
        embedding_service: Any = None,
    ):
        self.db = db
        self.repo = FAQRepository(db)
        self.normalizer = normalizer or QueryNormalizer()
        self.embedding_service = embedding_service

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> tuple[list[FAQRetrievalHit], float]:
        """
        Search for FAQ entries matching the query.

        Returns:
            Tuple of (list of hits, overall confidence score 0-1)
        """
        keywords = self.normalizer.extract_keywords(query)
        logger.debug(f"Extracted keywords: {keywords}")

        hits = []

        if keywords:
            keyword_results = await self.repo.search_by_keywords(
                keywords=keywords,
                category=category,
                limit=limit,
            )

            for entry, score in keyword_results:
                hit = FAQRetrievalHit(
                    id=str(entry.id),
                    category=entry.category,
                    question=entry.question,
                    answer=entry.answer,
                    score=float(score),
                    match_type="keyword",
                )
                hits.append(hit)

        if len(hits) < 2 and self.embedding_service:
            logger.debug("Using semantic search with embeddings")
            try:
                query_embedding = await self.embedding_service.get_embedding(query)
                semantic_results = await self.repo.search_by_embedding(
                    embedding=query_embedding,
                    limit=limit - len(hits),
                    threshold=0.5,
                )

                existing_ids = {h.id for h in hits}
                for entry, similarity in semantic_results:
                    if str(entry.id) not in existing_ids:
                        hit = FAQRetrievalHit(
                            id=str(entry.id),
                            category=entry.category,
                            question=entry.question,
                            answer=entry.answer,
                            score=similarity * 10,
                            match_type="semantic",
                        )
                        hits.append(hit)
            except Exception as e:
                logger.warning(f"Semantic search failed: {e}")

        if len(hits) < 2:
            logger.debug("Falling back to fuzzy search")
            fuzzy_results = await self.repo.search_fuzzy(
                query_text=query,
                category=category,
                limit=limit - len(hits),
            )

            existing_ids = {h.id for h in hits}
            for entry in fuzzy_results:
                if str(entry.id) not in existing_ids:
                    hit = FAQRetrievalHit(
                        id=str(entry.id),
                        category=entry.category,
                        question=entry.question,
                        answer=entry.answer,
                        score=1.0,
                        match_type="fuzzy",
                    )
                    hits.append(hit)

        confidence = self._calculate_confidence(hits, keywords)
        hits.sort(key=lambda h: h.score, reverse=True)

        return hits[:limit], confidence

    def _calculate_confidence(
        self,
        hits: list[FAQRetrievalHit],
        keywords: list[str],
    ) -> float:
        """Calculate overall confidence based on retrieval results."""
        if not hits:
            return 0.0

        top_score = hits[0].score if hits else 0
        max_possible_score = len(keywords) * 3 + 5 if keywords else 10

        score_factor = min(top_score / max(max_possible_score, 1), 1.0)

        match_type_scores = {
            "keyword": 1.0,
            "semantic": 0.9,
            "fuzzy": 0.6,
        }
        type_factor = match_type_scores.get(hits[0].match_type, 0.5)

        hit_count_factor = min(len(hits) / 3, 1.0)

        confidence = score_factor * 0.5 + type_factor * 0.3 + hit_count_factor * 0.2

        return round(confidence, 2)
