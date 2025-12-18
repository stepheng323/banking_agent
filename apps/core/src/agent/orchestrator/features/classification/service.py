"""Intent classification service for the orchestrator."""

from typing import Any, Optional

from langchain_core.runnables import Runnable
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.features.classification.prompts import CLASSIFICATION_SYSTEM_PROMPT
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorClassificationService:
    """Handles intent classification using LLM."""

    def __init__(self, classifier_llm: Runnable) -> None:
        self.classifier_llm = classifier_llm

    def _try_fast_path(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[ClassificationResult]:
        """
        Try to classify using fast path (regex) when intent is unambiguous.
        
        Only returns a result when we're CERTAIN of the intent based on:
        1. Active flow context (conversation_state)
        2. Simple, unambiguous patterns
        
        Returns None if LLM classification is needed.
        """
        import re
        
        text_clean = text.strip().lower()
        
        # Skip fast-path for complex messages with multiple intents
        # These should go to LLM for proper classification as 'mixed'
        transfer_keywords = {"send", "transfer", "pay"}
        query_keywords = {"balance", "transaction", "history", "spent", "spending"}
        has_transfer = any(kw in text_clean for kw in transfer_keywords)
        has_query = any(kw in text_clean for kw in query_keywords)
        # Also check for "and" which often indicates multiple operations
        has_conjunction = " and " in text_clean or " then " in text_clean
        
        if has_transfer and has_query:
            return None  # Mixed intent - let LLM handle it
        if has_transfer and has_conjunction and len(text_clean) > 30:
            return None  # Likely multiple recipients or operations
        
        greeting_patterns = {
            "hi", "hello", "hey", "hey there", "hi there", "hello there",
            "good morning", "good afternoon", "good evening", "good night",
            "gm", "gn", "morning", "afternoon", "evening",
            "bawo", "kaaro", "ekaro", "sannu", "barka", "kedu", "ndewo", "how far", "how you dey",
        }
        if text_clean in greeting_patterns:
            return ClassificationResult(
                intent="conversational",
                is_cancellation=False,
                is_complex=False,
                confidence=0.99,
                response="",
                complexity_reason="Simple greeting",
            )
        
        active_flow = None
        if context and context.get("conversationState"):
            active_flow = context["conversationState"].get("active_flow")
        
        cancel_patterns = {"cancel", "stop", "abort", "nevermind", "forget it", "no thanks"}
        if text_clean in cancel_patterns:
            return ClassificationResult(
                intent="cancel",
                is_cancellation=True,
                is_complex=False,
                confidence=0.99,
                response="Transaction cancelled.",
                complexity_reason="Simple single intent",
            )
        
        if not active_flow:
            if text_clean in {"yes", "ok", "sure", "confirm", "proceed"}:
                return ClassificationResult(
                    intent="yes",
                    is_complex=False,
                    confidence=0.95,
                    response="Confirmed.",
                    complexity_reason="Simple single intent",
                )
            if text_clean in {"no", "skip", "nope"}:
                return ClassificationResult(
                    intent="no",
                    is_complex=False,
                    confidence=0.95,
                    response="Declined.",
                    complexity_reason="Simple single intent",
                )
        

        if active_flow in {"transfer", "airtime", "data"}:
            # Don't fast-path if message looks like a different intent
            manage_account_patterns = {"account", "accounts", "link", "unlink", "default", "how many", "show", "list"}
            question_patterns = {"why", "what", "how", "when", "where", "who", "explain", "help"}
            
            words = set(text_clean.split())
            is_manage_accounts = bool(words & manage_account_patterns)
            is_question = text_clean.endswith("?") or bool(words & question_patterns)
            
            # Only fast-path if it's clearly flow-related data (amount, not a question or account query)
            if not is_manage_accounts and not is_question:
                amount_match = re.match(r'^[nN]?\\s*(\\d+)[kK]?$', text_clean)
                if amount_match:
                    return ClassificationResult(
                        intent=active_flow,
                        is_complex=False,
                        confidence=0.98,
                        response="Amount received.",
                        complexity_reason="Simple amount detected",
                    )
        
        if active_flow == "transfer":
            if re.match(r'^\d{10}$', text_clean):
                return ClassificationResult(
                    intent="transfer",
                    is_complex=False,
                    confidence=0.97,
                    response="Account number received.",
                    complexity_reason="Account number detected",
                )
        
        if active_flow in {"airtime", "data"}:
            if re.match(r'^0\d{10}$|^\d{10}$', text_clean):
                return ClassificationResult(
                    intent=active_flow,
                    is_complex=False,
                    confidence=0.97,
                    response="Phone number received.",
                    complexity_reason="Phone number detected",
                )
        return None

    async def classify(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
        last_response: Optional[str] = None,
        image_data: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify user intent from text."""
        fast_result = self._try_fast_path(text, context)
        if fast_result and not image_data:
            logger.info("fast")
            return fast_result
        


        system = CLASSIFICATION_SYSTEM_PROMPT
        user_content = text.strip()

        if last_response:
            user_content = f"{user_content}\n\n[Previous assistant response: {last_response}]"

        if context:
            if context.get("conversationState"):
                conv_state = context["conversationState"]
                active = conv_state.get('active_flow')
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: Active flow: {active}, "
                    f"Flow state: {conv_state.get('flow_state')}]\n"
                    f"NOTE: Even with an active {active} flow, classify as manage_accounts if user asks about their accounts, "
                    f"or conversational if they ask questions like 'why?' or 'what?'. "
                    f"Only classify as {active} if the message provides actual transaction data (amount, account, bank, phone)."
                )

            if context.get("pendingBeneficiarySuggestion"):
                suggestion = context["pendingBeneficiarySuggestion"]
                recipient_name = suggestion.get(
                    "recipient_name", "this recipient")
                beneficiary_type = suggestion.get("beneficiary_type", "transfer")
                
                context_message = f"The assistant just asked about saving a beneficiary: {recipient_name}."
                if last_response:
                    context_message = f"The assistant's last message was: '{last_response}'"
                
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: {context_message}]\n"
                    f"Beneficiary type: {beneficiary_type}. "
                    f"This message is a response to that question. "
                    f"CRITICAL: If the last message asked for a name/alias and user provides just a name (even a single word like 'Gaines'), you MUST extract it as extracted_alias. "
                    f"Classify as 'yes'/'confirm' if user wants to save, 'no'/'skip' if user declines, "
                    f"or extract the alias if user provides a name. When in doubt and user provides a name-like word, extract it as extracted_alias."
                )

        
        messages = [
            {"role": "system", "content": system}
        ]
        
        if image_data:
            user_message = {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data}
                    }
                ]
            }
        else:
             user_message = {"role": "user", "content": user_content}
        
        messages.append(user_message)

        structured_llm = self.classifier_llm.with_structured_output(ClassificationResult)
        return await structured_llm.ainvoke(messages)
