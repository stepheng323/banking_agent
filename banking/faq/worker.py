"""FAQ Worker.

Stateless worker for FAQ tasks.
"""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import FAQOutcome, FAQResult
from banking.faq.nodes.gate import confidence_gate_node
from banking.faq.nodes.guard import final_guard_node
from banking.faq.nodes.normalize import normalize_query_node
from banking.faq.nodes.retrieve import create_retrieve_node
from banking.faq.nodes.synthesize import create_synthesize_node
from banking.faq.nodes.validate import validate_intent_node
from banking.faq.retrieval.embeddings import EmbeddingService
from banking.faq.state import FAQState
from banking.policy.service import capability_block_message
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FAQWorker:
    """Stateless worker for FAQ tasks."""

    def __init__(self, llm: Any, get_db: Any) -> None:
        self.embedding_service = EmbeddingService()

        self.retrieve_node = create_retrieve_node(get_db, self.embedding_service)
        self.synthesize_node = create_synthesize_node(llm)

    @staticmethod
    def _result_from_state(state: FAQState) -> FAQResult:
        guarded_state = final_guard_node(state)
        return FAQResult(
            outcome=FAQOutcome.OK,
            response=guarded_state.get("response", ""),
            should_route_to_support=guarded_state.get("should_route_to_support", False),
        )

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> FAQResult:
        """Run the FAQ flow."""
        del payload, pin_verified
        phone_number = context.get("phone_number", "")
        message = (user_message or "").strip()
        locale = LocaleManager.normalize(context.get("language")).value
        if policy_message := capability_block_message(domain="faq", action="answer_question", locale=locale):
            logger.info("faq_worker_capability_blocked")
            return FAQResult(
                outcome=FAQOutcome.OK,
                response=policy_message,
            )

        state: FAQState = {
            "phone_number": phone_number,
            "language": locale,
            "message": message,
            "message_id": "",
            "is_forbidden_scope": False,
            "should_route_to_support": False,
        }

        try:
            state = validate_intent_node(state)
            if state.get("is_forbidden_scope"):
                return FAQResult(
                    outcome=FAQOutcome.OK,
                    response=render_message(
                        "faq.support_handoff", LocaleManager.normalize(state.get("language")).value
                    ),
                    should_route_to_support=True,
                )

            state = normalize_query_node(state)

            state = await self.retrieve_node(state)

            state = confidence_gate_node(state)
            if state.get("response"):  # If gate added a response (uncertainty), we stop
                return self._result_from_state(state)

            state = await self.synthesize_node(state)

            return self._result_from_state(state)

        except Exception as e:
            logger.error(f"FAQ worker failed: {e}", exc_info=True)
            return FAQResult(outcome=FAQOutcome.FAILED, error=str(e))
