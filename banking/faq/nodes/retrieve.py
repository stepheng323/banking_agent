"""Retrieve node - search FAQ entries."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from banking.faq.retrieval.hybrid import HybridRetriever
from banking.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def create_retrieve_node(get_db: Callable[[], AsyncSession], embedding_service=None):
    """Factory to create retrieve node with database and embedding dependencies."""

    async def retrieve_node(state: FAQState) -> FAQState:
        """Retrieve FAQ entries matching the query."""
        message = state.get("normalized_query") or state.get("message", "")
        category = state.get("detected_category")

        async with get_db() as db:
            retriever = HybridRetriever(
                db=db,
                embedding_service=embedding_service,
            )
            hits, confidence = await retriever.search(
                query=message,
                category=category,
                limit=5,
            )

        retrieved_entries = [
            {
                "id": hit.id,
                "category": hit.category,
                "question": hit.question,
                "answer": hit.answer,
                "score": hit.score,
                "match_type": hit.match_type,
            }
            for hit in hits
        ]

        logger.info(f"Retrieved {len(retrieved_entries)} FAQ entries, confidence: {confidence}")

        return {
            **state,
            "retrieved_entries": retrieved_entries,
            "retrieval_confidence": confidence,
        }

    return retrieve_node


async def retrieve_node(state: FAQState) -> FAQState:
    """Placeholder - use create_retrieve_node factory with dependencies."""
    raise NotImplementedError("Use create_retrieve_node factory with db dependency")
