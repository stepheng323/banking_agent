"""Support intent classifier using LLM."""

import json

from langchain_core.runnables import Runnable

from apps.core.src.agent.sub_agents.support.models import (
    ClassificationResult,
    SupportIntent,
    TransactionReference,
)
from apps.core.src.agent.sub_agents.support.prompts.classifier import SUPPORT_CLASSIFIER_PROMPT
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SupportClassifier:
    """Classifies user messages into support intents."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    async def classify(self, message: str) -> ClassificationResult:
        """
        Classify message into a support intent.

        Returns ClassificationResult with intent (or None if not support-related).
        """
        prompt = SUPPORT_CLASSIFIER_PROMPT.format(message=message)

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # Parse JSON response
            result = self._parse_response(content, message)
            logger.info(
                "support_classified",
                intent=result.intent.value if result.intent else None,
                confidence=result.confidence,
            )
            return result

        except Exception as e:
            logger.error("support_classification_failed", error=str(e))
            return ClassificationResult(
                intent=None,
                confidence=0.0,
                raw_message=message,
            )

    def _parse_response(self, content: str, original_message: str) -> ClassificationResult:
        """Parse LLM response into ClassificationResult."""
        try:
            # Extract JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)
            else:
                data = {}

            # Parse intent
            intent_str = data.get("intent")
            intent = None
            if intent_str:
                try:
                    intent = SupportIntent(intent_str)
                except ValueError:
                    pass

            # Parse transaction reference
            tx_ref_data = data.get("transaction_ref", {})
            tx_ref = None
            if tx_ref_data and any(tx_ref_data.values()):
                tx_ref = TransactionReference(
                    amount=tx_ref_data.get("amount"),
                    recipient_name=tx_ref_data.get("recipient_name"),
                    date_hint=tx_ref_data.get("date_hint"),
                )

            return ClassificationResult(
                intent=intent,
                confidence=data.get("confidence", 0.5),
                transaction_ref=tx_ref,
                raw_message=original_message,
            )

        except json.JSONDecodeError:
            return ClassificationResult(
                intent=None,
                confidence=0.0,
                raw_message=original_message,
            )

    def is_support_intent(self, result: ClassificationResult) -> bool:
        """Check if classification result is a valid support intent."""
        return result.intent is not None and result.confidence >= 0.5
