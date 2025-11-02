"""Quick intent classification node for early routing optimization."""

import re
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from apps.core.src.agent.orchestrator.state import OrchestratorState


class QuickIntent(BaseModel):
    """Quick intent classification result."""
    intent: Literal["conversational", "query", "transfer", "utility"]
    confidence: float  # 0.0-1.0


class QuickIntentClassifierNode:
    """
    Fast intent classifier that detects conversational messages early
    to skip expensive normalization steps.

    Uses keyword matching for obvious cases (fast) and lightweight LLM
    for edge cases.
    """

    # Obvious conversational patterns
    CONVERSATIONAL_KEYWORDS = {
        "greetings": ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "gm", "sup", "what's up"],
        "thanks": ["thanks", "thank you", "thank", "appreciate", "cheers", "much appreciated"],
        "goodbye": ["bye", "goodbye", "see you", "later", "have a good day", "take care"],
        "acknowledgments": ["ok", "okay", "alright", "got it", "understood", "cool"],
        "help": ["help", "what can you do", "what do you do", "capabilities", "features"]
    }

    # Banking intent keywords (if detected, likely not conversational)
    BANKING_KEYWORDS = {
        "query": ["balance", "check balance", "account", "statement", "balance", "how much"],
        "transfer": ["send", "transfer", "pay", "send money", "to", "recipient", "beneficiary"],
        "utility": ["airtime", "data", "top up", "recharge", "bundle"]
    }

    def __init__(self, llm: ChatOpenAI | None = None):
        """Initialize with optional LLM for edge cases."""
        self.llm = llm
        if llm:
            # Use lightweight model for classification
            # Note: model and temperature should already be set on llm, not here
            self.classifier_llm = llm.with_structured_output(QuickIntent)

    def _keyword_classify(self, message: str) -> QuickIntent | None:
        """Fast keyword-based classification for obvious cases."""
        message_lower = message.lower().strip()

        # Check for obvious conversational patterns
        for category, keywords in self.CONVERSATIONAL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in message_lower:
                    # Double-check it's not part of a banking query
                    if not any(banking_word in message_lower for banking_keywords in self.BANKING_KEYWORDS.values() for banking_word in banking_keywords):
                        return QuickIntent(intent="conversational", confidence=0.95)

        # Check for obvious banking intents
        for intent, keywords in self.BANKING_KEYWORDS.items():
            for keyword in keywords:
                if keyword in message_lower:
                    return QuickIntent(intent=intent, confidence=0.90)

        return None

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """
        Quickly classify intent to optimize routing.

        For obvious conversational messages, skip expensive normalization steps.
        """
        message = state["message"]

        # Fast keyword check first
        keyword_result = self._keyword_classify(message)

        if keyword_result:
            print(
                f"⚡ Quick classification: {keyword_result.intent} ({keyword_result.confidence:.0%} confidence)")
            state["primary_intent"] = keyword_result.intent
            state["quick_classification"] = keyword_result.intent
            return state

        # For ambiguous cases, use lightweight LLM classification
        if self.llm:
            print("🔍 Quick LLM classification (ambiguous case)...")

            classification_prompt = f"""Classify this user message into ONE of these intents:
- conversational: Greetings, thanks, goodbye, small talk, help requests
- query: Balance checks, account info, statements
- transfer: Money transfers, sending money, payments
- utility: Airtime, data bundles, recharges

Message: "{message}"

Respond with ONLY the intent name (conversational/query/transfer/utility)."""

            try:
                result = await self.classifier_llm.ainvoke([HumanMessage(content=classification_prompt)])
                state["primary_intent"] = result.intent
                state["quick_classification"] = result.intent
                print(
                    f"⚡ LLM classification: {result.intent} ({result.confidence:.0%} confidence)")
                return state
            except Exception as e:
                print(f"⚠️  Quick classification failed: {e}")

        # Fallback: assume banking intent, go through full pipeline
        print("⚠️  Could not quickly classify, using full pipeline")
        state["quick_classification"] = None
        return state
