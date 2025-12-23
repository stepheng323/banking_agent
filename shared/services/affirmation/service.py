"""
Unified Affirmation Service.

Provides centralized detection of user approval/rejection with:
1. Fast path: Hardcoded phrase matching
2. LLM fallback: For multilingual/nuanced responses
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AffirmationOutput(BaseModel):
    """Pydantic model for structured LLM output."""
    intent: Literal["approve", "reject", "unclear", "custom"] = Field(
        description="User's intent: approve (agrees), reject (declines), unclear (ambiguous), custom (wants modification)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score between 0 and 1"
    )
    custom_data: Optional[dict] = Field(
        default=None,
        description="Additional data for custom requests, e.g., {'modification': '40k from GTB'}"
    )


@dataclass
class AffirmationResult:
    """Result of affirmation classification."""
    intent: Literal["approve", "reject", "unclear", "custom"]
    confidence: float
    custom_data: Optional[dict] = field(default=None)
    
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
        result = await AffirmationService.classify("oya do it")
        if result.is_approval:
            # proceed
        elif result.is_rejection:
            # cancel
        else:
            # ask for clarification
    """
    
    APPROVAL_PHRASES_EN = {
        "yes", "yeah", "yep", "yup", "yea", "ok", "okay", "sure",
        "proceed", "continue", "go on", "go ahead", "confirm", "approve",
        "do it", "let's go", "lets go", "fine", "alright", "all right",
        "correct", "sounds good", "that's fine", "thats fine",
        "yes please", "ok please", "please do", "go for it",
        "absolutely", "definitely", "of course", "certainly",
        "go", "do", "send", "send it", "do the transfer", "make it happen",
    }
    
    REJECTION_PHRASES_EN = {
        "no", "nope", "nah", "cancel", "stop", "don't", "dont",
        "skip", "never mind", "nevermind", "not now", "maybe later",
        "no thanks", "no thank you", "nah thanks", "forget it",
        "not interested", "i don't want", "i dont want",
    }
    
    APPROVAL_PHRASES_NG = {
        "abeg", "oya", "na so", "e go", "e dey", "dey go", "make e happen",
        "go ahead jare", "do am", "carry go", "sha do", "no wahala",
        "okay na", "yes na", "i dey", "wetin dey", "e correct",
        "bẹẹni", "o dara", "jẹki", "se", "dara",
        "biko", "ee", "ọ dị mma",
        "eh", "to", "na'am", "yauwa",
    }
    
    REJECTION_PHRASES_NG = {
        "abeg no", "e no go", "forget am", "leave am", "no be so",
        "i no wan", "e no dey", "no vex", "abeg comot", "wahala",
        "no go work", "i no gree", "sha no",
        "rara", "ko", "má",
        "mba", "hapụ ya",
        "a'a", "ba", "bari",
    }
    
    APPROVAL_PHRASES = APPROVAL_PHRASES_EN | APPROVAL_PHRASES_NG
    REJECTION_PHRASES = REJECTION_PHRASES_EN | REJECTION_PHRASES_NG
    
    @classmethod
    def _normalize(cls, text: str) -> str:
        """Normalize text for matching."""
        return text.lower().strip()
    
    @classmethod
    def _is_approval_fast(cls, text_lower: str) -> bool:
        """Fast path approval check."""
        if text_lower in cls.APPROVAL_PHRASES:
            return True
        return any(phrase in text_lower for phrase in cls.APPROVAL_PHRASES if len(phrase) > 3)
    
    @classmethod
    def _is_rejection_fast(cls, text_lower: str) -> bool:
        """Fast path rejection check."""
        if text_lower in cls.REJECTION_PHRASES:
            return True
        return any(phrase in text_lower for phrase in cls.REJECTION_PHRASES if len(phrase) > 3)
    
    @classmethod
    async def classify(
        cls,
        text: str,
        context: Optional[str] = None,
        use_llm_fallback: bool = True,
        llm: Optional[BaseChatModel] = None,
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
        text_lower = cls._normalize(text)
        
        if cls._is_approval_fast(text_lower):
            logger.debug("affirmation_fast_path", intent="approve", text=text[:50])
            return AffirmationResult(intent="approve", confidence=0.95)
        
        if cls._is_rejection_fast(text_lower):
            logger.debug("affirmation_fast_path", intent="reject", text=text[:50])
            return AffirmationResult(intent="reject", confidence=0.95)
        
        if use_llm_fallback and llm is not None:
            try:
                result = await cls._llm_classify(text, context, llm)
                logger.info("affirmation_llm_fallback", intent=result.intent, confidence=result.confidence)
                return result
            except Exception as e:
                logger.warning("affirmation_llm_fallback_failed", error=str(e))
        
        return AffirmationResult(intent="unclear", confidence=0.5)
    
    @classmethod
    async def _llm_classify(
        cls,
        text: str,
        context: Optional[str],
        llm: BaseChatModel,
    ) -> AffirmationResult:
        """
        LLM-based classification using structured output.
        
        Handles edge cases like:
        - Mixed language: "Okay but abeg make am quick"
        - Custom requests: "Use 40k from GTB instead"
        - Ambiguous: "Maybe" or "I'm not sure"
        """
        context_str = context or "confirm an action"
        
        prompt = f"""Classify this user message as approval, rejection, or custom request.

Context: We asked the user to {context_str}
User said: "{text}"

Guidelines:
- approve: User agrees/confirms (English, Nigerian Pidgin, Yoruba, Igbo, Hausa)
- reject: User declines/cancels
- custom: User wants a modification (e.g., different amount, ratio)
- unclear: Can't determine intent

Examples:
- "Abeg do am" → approve (0.9)
- "E no work for me" → reject (0.85)
- "Use 40k from GTB instead" → custom with custom_data={{"modification": "40k from GTB"}}
- "Hmm let me think" → unclear (0.6)"""

        structured_llm = llm.with_structured_output(AffirmationOutput)
        output: AffirmationOutput = await structured_llm.ainvoke(prompt)
        
        return AffirmationResult(
            intent=output.intent,
            confidence=output.confidence,
            custom_data=output.custom_data,
        )
    
    @classmethod
    def classify_sync(cls, text: str) -> AffirmationResult:
        """
        Synchronous fast-path-only classification.
        
        Use this when you don't need LLM fallback and want instant results.
        """
        text_lower = cls._normalize(text)
        
        if cls._is_approval_fast(text_lower):
            return AffirmationResult(intent="approve", confidence=0.95)
        
        if cls._is_rejection_fast(text_lower):
            return AffirmationResult(intent="reject", confidence=0.95)
        
        return AffirmationResult(intent="unclear", confidence=0.5)

