"""Normalization nodes for typo correction and disambiguation."""

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.services.user_context_loader import UserContextLoader
from apps.core.src.agent.services.typo_corrector import ContextAwareTypoCorrector
from apps.core.src.agent.services.intent_disambiguator import IntentDisambiguator


class ContextLoaderNode:
    """Loads user context (accounts, beneficiaries, etc.)."""

    def __init__(self, context_loader: UserContextLoader):
        """Initialize with context loader service."""
        self.context_loader = context_loader

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Load user context from database."""
        phone_number = state["phone_number"]

        print("📚 Loading user context...")
        user_context = await self.context_loader.load_context(phone_number)
        print(f"   ✓ {len(user_context.beneficiaries)} beneficiaries, "
              f"{len(user_context.accounts)} accounts (₦{user_context.get_total_balance():,.2f} total)")

        state["user_context"] = user_context
        return state


class TypoCorrectionNode:
    """Corrects typos using user-specific context."""

    def __init__(self, typo_corrector: ContextAwareTypoCorrector):
        """Initialize with typo corrector service."""
        self.typo_corrector = typo_corrector

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Correct typos in the user message."""
        message = state["message"]
        user_context = state.get("user_context")

        if not user_context:
            state["corrected_intent"] = None
            return state

        print("✏️  Checking for typos...")
        corrected_intent = await self.typo_corrector.correct_with_context(
            message, user_context
        )

        if corrected_intent.corrections:
            corrections_str = ", ".join([
                f"{c.entity}→{c.corrected_to}" for c in corrected_intent.corrections
            ])
            print(f"   ✓ Corrections: {corrections_str}")

        state["corrected_intent"] = corrected_intent

        # Check if clarification needed
        if corrected_intent.needs_clarification:
            print("⚠️  Typo correction needs clarification")
            state["awaiting_clarification"] = True
            state["response"] = corrected_intent.clarification_question or \
                "I'm not sure I understood that correctly. Could you clarify?"

        return state


class DisambiguationNode:
    """Disambiguates complex or multi-recipient scenarios."""

    def __init__(self, intent_disambiguator: IntentDisambiguator, get_context_func):
        """Initialize with disambiguator service and context function."""
        self.intent_disambiguator = intent_disambiguator
        self._get_context = get_context_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Disambiguate multi-recipient/complex scenarios."""
        corrected_intent = state.get("corrected_intent")
        user_context = state.get("user_context")

        if not corrected_intent or not user_context:
            state["disambiguated_intent"] = None
            return state

        print("🔍 Disambiguating intent...")
        normalized_message = corrected_intent.corrected
        disambiguated_intent = await self.intent_disambiguator.disambiguate(
            normalized_message, user_context
        )

        state["disambiguated_intent"] = disambiguated_intent

        if disambiguated_intent.needs_clarification:
            print("⚠️  Intent disambiguation needs clarification")
            phone_number = state["phone_number"]
            context = self._get_context(phone_number)
            context.awaiting_clarification = True
            context.clarification_type = "intent_disambiguation"

            state["awaiting_clarification"] = True
            state["response"] = disambiguated_intent.clarification_question or \
                "I need some clarification about your request."

        return state
