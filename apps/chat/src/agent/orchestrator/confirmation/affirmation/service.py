"""
Unified Affirmation Service.

Provides centralized detection of user approval/rejection with:
1. Fast path: Hardcoded phrase matching
2. LLM fallback: For multilingual/nuanced responses
"""

from dataclasses import dataclass, field
from typing import Literal

from langchain_core.language_models import BaseChatModel

from apps.chat.src.agent.orchestrator.confirmation.confirmation_classifier import (
    classify_confirmation_reply,
    classify_confirmation_reply_sync,
)
from apps.chat.src.agent.orchestrator.confirmation.confirmation_models import (
    ConfirmationDecision,
    ConfirmationDecisionOutput,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AffirmationResult:
    """Result of affirmation classification."""

    intent: Literal["approve", "reject", "unclear", "custom"]
    confidence: float
    custom_data: dict | None = field(default=None)

    @property
    def is_approval(self) -> bool:
        return self.intent == "approve"

    @property
    def is_rejection(self) -> bool:
        return self.intent == "reject"

    @property
    def is_unclear(self) -> bool:
        return self.intent in ("unclear", "custom")


class AffirmationService:
    """
    Centralized yes/no/unclear detection with LLM fallback.

    Usage:
        result = await AffirmationService.classify("yes please")
        if result.is_approval:
            # proceed
        elif result.is_rejection:
            # cancel
        else:
            # ask for clarification
    """

    @classmethod
    def _normalize(cls, text: str) -> str:
        """Normalize text for matching."""
        return text.lower().strip()

    @staticmethod
    def _from_confirmation_decision(decision: ConfirmationDecision) -> AffirmationResult:
        if decision.action == "approve":
            return AffirmationResult(intent="approve", confidence=decision.confidence)
        if decision.action == "reject":
            return AffirmationResult(intent="reject", confidence=decision.confidence)
        if decision.action == "modify":
            return AffirmationResult(intent="custom", confidence=decision.confidence, custom_data=decision.custom_data)
        return AffirmationResult(intent="unclear", confidence=decision.confidence, custom_data=decision.custom_data)

    @classmethod
    async def classify(
        cls,
        text: str,
        context: str | None = None,
        use_llm_fallback: bool = True,
        llm: BaseChatModel | None = None,
    ) -> AffirmationResult:
        """
        Classify user response as approval, rejection, or unclear.

        Args:
            text: User's message
            context: What we're asking about (for LLM prompt), e.g., "approve multi-account funding"
            use_llm_fallback: Whether to use LLM for unclear cases
            llm: LLM instance for fallback (required if use_llm_fallback=True)

        Returns:
            AffirmationResult with intent, confidence, and optional custom_data
        """
        structured_llm = (
            llm.with_structured_output(ConfirmationDecisionOutput) if use_llm_fallback and llm is not None else None
        )
        decision = await classify_confirmation_reply(
            text,
            prompt_kind="amount_suggestion",
            context=context or "confirm an action",
            structured_llm=structured_llm,
        )
        result = cls._from_confirmation_decision(decision)
        logger.info(
            "affirmation_classified",
            intent=result.intent,
            confidence=result.confidence,
            source=decision.source,
        )
        return result

    @classmethod
    def classify_sync(cls, text: str) -> AffirmationResult:
        """
        Synchronous fast-path-only classification.

        Use this when you don't need LLM fallback and want instant results.
        """
        decision = classify_confirmation_reply_sync(
            cls._normalize(text),
            prompt_kind="amount_suggestion",
        )
        return cls._from_confirmation_decision(decision)
