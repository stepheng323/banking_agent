"""Validate intent node - detect forbidden scopes."""

import re
from typing import cast

from apps.chat.src.agent.graphs.faq.prompts import FORBIDDEN_PATTERNS
from apps.chat.src.agent.graphs.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def validate_intent_node(state: FAQState) -> FAQState:
    """
    Validate that the query is appropriate for FAQ handling.

    Detects forbidden scopes that should route to support:
    - User-specific queries ("my transfer", "my balance")
    - Transaction status queries ("why did it fail")
    - Dispute queries ("refund", "chargeback")
    """
    message = state.get("message", "").lower()

    is_forbidden = False
    forbidden_reason = None
    detected_patterns = []

    # Check each category of forbidden patterns
    for category, config in FORBIDDEN_PATTERNS.items():
        for pattern in config["patterns"]:
            if re.search(pattern, message, re.IGNORECASE):
                is_forbidden = True
                forbidden_reason = config["reason"]
                detected_patterns.append(pattern)
                logger.debug(f"Forbidden pattern detected: {pattern} ({category})")

    if is_forbidden:
        logger.info(f"Query flagged as forbidden scope: {forbidden_reason}")

    return {
        **state,
        "is_forbidden_scope": is_forbidden,
        "forbidden_reason": cast(str | None, forbidden_reason),
        "should_route_to_support": is_forbidden,
    }
