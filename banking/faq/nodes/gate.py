"""Confidence gate node - check if retrieval quality is sufficient."""

from banking.faq.state import FAQState
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
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
    locale = LocaleManager.normalize(state.get("language")).value

    # No results at all
    if not retrieved:
        logger.info("No FAQ entries retrieved - returning uncertainty response")
        return {
            **state,
            "response": render_message("faq.uncertainty_response", locale),
            "response_source": "uncertainty",
        }

    # Very low confidence
    if confidence < LOW_CONFIDENCE:
        logger.info(f"Confidence too low ({confidence}) - returning uncertainty response")
        return {
            **state,
            "response": render_message("faq.uncertainty_response", locale),
            "response_source": "uncertainty",
        }

    if len(retrieved) == 1 and confidence >= HIGH_CONFIDENCE:
        top_hit = retrieved[0]
        answer = str(top_hit.get("answer") or "").strip()
        if answer:
            logger.info(
                "FAQ single high-confidence hit - returning seeded answer",
                faq_id=top_hit.get("id"),
                confidence=confidence,
            )
            return {
                **state,
                "response": answer,
                "response_source": "seeded_faq",
                "top_hit_id": str(top_hit.get("id") or ""),
            }

    # Confidence is acceptable - continue to synthesis
    logger.debug(f"Confidence acceptable ({confidence}) - proceeding to synthesis")
    return state
