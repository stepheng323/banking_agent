"""Normalize query node - clean and extract keywords."""

import re

from apps.chat.src.agent.workers.faq.prompts import CATEGORY_PATTERNS
from apps.chat.src.agent.workers.faq.retrieval.normalizer import QueryNormalizer
from apps.chat.src.agent.workers.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Singleton normalizer instance
_normalizer = QueryNormalizer()


def normalize_query_node(state: FAQState) -> FAQState:
    """
    Normalize the user query and extract keywords.

    - Removes filler words
    - Collapses synonyms
    - Extracts keywords for retrieval
    - Detects likely category
    """
    message = state.get("message", "")

    # Normalize query
    normalized = _normalizer.normalize(message)

    # Extract keywords
    keywords = _normalizer.extract_keywords(message)

    # Detect category
    detected_category = _detect_category(message)

    logger.debug(f"Normalized: '{message}' -> '{normalized}'")
    logger.debug(f"Keywords: {keywords}")
    logger.debug(f"Detected category: {detected_category}")

    return {
        **state,
        "normalized_query": normalized,
        "extracted_keywords": keywords,
        "detected_category": detected_category,
    }


def _detect_category(message: str) -> str | None:
    """Detect the most likely FAQ category based on message content."""
    message_lower = message.lower()

    category_scores: dict[str, int] = {}

    for category, patterns in CATEGORY_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if re.search(pattern, message_lower):
                score += 1
        if score > 0:
            category_scores[category] = score

    if not category_scores:
        return None

    # Return category with highest score
    return max(category_scores, key=lambda k: category_scores[k])
