"""Confidence gate node - check if retrieval quality is sufficient."""

from apps.core.src.agent.sub_agents.faq.prompts import UNCERTAINTY_RESPONSE
from apps.core.src.agent.sub_agents.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Confidence thresholds
HIGH_CONFIDENCE = 0.6
LOW_CONFIDENCE = 0.3


def confidence_gate_node(state: FAQState) -> FAQState:
    """
    Check if retrieval confidence is sufficient.

    If confidence is too low, set uncertainty response instead of synthesizing.
    """
    confidence = state.get("retrieval_confidence", 0.0)
    retrieved = state.get("retrieved_entries", [])

    # No results at all
    if not retrieved:
        logger.info("No FAQ entries retrieved - returning uncertainty response")
        return {
            **state,
            "response": UNCERTAINTY_RESPONSE,
        }

    # Very low confidence
    if confidence < LOW_CONFIDENCE:
        logger.info(f"Confidence too low ({confidence}) - returning uncertainty response")
        return {
            **state,
            "response": UNCERTAINTY_RESPONSE,
        }

    # Confidence is acceptable - continue to synthesis
    logger.debug(f"Confidence acceptable ({confidence}) - proceeding to synthesis")
    return state
