"""Synthesize answer node - generate response using LLM."""

from langchain_core.runnables import Runnable

from apps.chat.src.agent.workers.faq.prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_PROMPT,
)
from apps.chat.src.agent.workers.faq.state import FAQState
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def create_synthesize_node(llm: Runnable):
    """Factory to create synthesize node with LLM dependency."""

    async def synthesize_answer_node(state: FAQState) -> FAQState:
        """
        Synthesize an answer from retrieved FAQ entries using LLM.

        This is the ONLY node that uses the LLM.
        """
        # If response already set (e.g., by confidence gate), skip
        if state.get("response"):
            return state
        locale = LocaleManager.normalize(state.get("language")).value

        retrieved = state.get("retrieved_entries", [])
        message = state.get("message", "")

        if not retrieved:
            logger.warning("No entries to synthesize from")
            return state

        # Format FAQ content for prompt
        faq_content = _format_faq_content(retrieved)

        # Build prompt
        user_prompt = SYNTHESIS_USER_PROMPT.format(
            question=message,
            faq_content=faq_content,
        )

        # Call LLM
        try:
            messages = [
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            response = await llm.ainvoke(messages)
            answer = response.content if hasattr(response, "content") else str(response)

            logger.info(f"Synthesized answer ({len(answer)} chars)")

            return {
                **state,
                "response": answer,
                "response_source": "llm_synthesis",
            }

        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            return {
                **state,
                "error": str(e),
                "response": render_message("faq.synthesis_failed", locale),
                "response_source": "synthesis_failed",
            }

    return synthesize_answer_node


def _format_faq_content(entries: list[dict]) -> str:
    """Format FAQ entries for LLM prompt."""
    parts = []
    for i, entry in enumerate(entries[:3], 1):  # Max 3 entries
        parts.append(f"[FAQ {i}]")
        parts.append(f"Q: {entry['question']}")
        parts.append(f"A: {entry['answer']}")
        parts.append("")

    return "\n".join(parts)


# Placeholder for direct import
async def synthesize_answer_node(state: FAQState) -> FAQState:
    """Placeholder - use create_synthesize_node factory with LLM dependency."""
    raise NotImplementedError("Use create_synthesize_node factory with LLM dependency")
