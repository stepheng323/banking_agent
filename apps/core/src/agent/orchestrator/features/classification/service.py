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
        
        # Cancel: Let LLM handle if there's an active flow (for contextual response)
        cancel_patterns = {"cancel", "stop", "abort", "nevermind", "forget it", "no thanks"}
        if text_clean in cancel_patterns:
            if active_flow:
                # Let LLM generate contextual cancellation message
                return None
            return ClassificationResult(
                intent="cancel",
                is_cancellation=True,
                is_complex=False,
                confidence=0.99,
                response="There's nothing to cancel. How can I help?",
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
            # Only intercept CLEAR interrupts during active flows
            # Let LLM decide for ambiguous cases (corrections, updates, etc.)
            manage_account_keywords = {"account", "accounts", "link", "linked", "unlink", "default"}
            manage_account_phrases = {"show my", "list my", "my accounts", "linked account"}
            query_patterns = {"balance", "history", "statement", "spent", "spending", "transaction"}
            
            words = set(text_clean.split())
            
            is_manage_accounts = (
                bool(words & manage_account_keywords) or 
                any(phrase in text_clean for phrase in manage_account_phrases)
            )
            is_query = bool(words & query_patterns)
            
            # INTERRUPT DETECTION: These clearly take priority over the active flow
            if is_manage_accounts:
                return ClassificationResult(
                    intent="manage_accounts",
                    is_complex=False,
                    confidence=0.95,
                    response="",
                    complexity_reason="Account management request during active flow",
                )
            
            if is_query:
                return ClassificationResult(
                    intent="query",
                    is_complex=False,
                    confidence=0.95,
                    response="",
                    complexity_reason="Query request during active flow",
                )
            
            # Fast path for account/phone numbers during active flows
            # These are clearly continuations of the flow, not cancellations
            if active_flow == "transfer":
                # Match 10-digit account number (optionally with bank name after comma or space)
                # Examples: "0860506361", "0860506361, Access", "0860506361 access bank"
                if re.match(r'^\d{10}(\s*,?\s*\w+)?', text_clean):
                    return ClassificationResult(
                        intent="transfer",
                        is_complex=False,
                        confidence=0.97,
                        response="",  # Let the transfer flow handle response
                        complexity_reason="Account number detected - flow continuation",
                    )
            
            if active_flow in {"airtime", "data"}:
                if re.match(r'^0?\d{10,11}$', text_clean):
                    return ClassificationResult(
                        intent=active_flow,
                        is_complex=False,
                        confidence=0.97,
                        response="",
                        complexity_reason="Phone number detected - flow continuation",
                    )
            
            # For anything else during active flow (amounts, corrections, new transactions),
            # let the LLM decide with full context about the pending transaction
            return None  # Fall through to LLM classification
        
        return None

    async def classify(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
        last_response: Optional[str] = None,
        image_data: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify user intent from text."""
        # Debug log the conversation state for troubleshooting
        conv_state = context.get("conversationState") if context else None
        if conv_state:
            logger.info("classification_with_active_flow", 
                       active_flow=conv_state.get("active_flow"),
                       flow_state=conv_state.get("flow_state"),
                       text=text[:50])
        else:
            logger.debug("classification_no_conv_state", text=text[:50])
        
        fast_result = self._try_fast_path(text, context)
        if fast_result and not image_data:
            logger.info("fast_path_used", intent=fast_result.intent)
            return fast_result
        


        system = CLASSIFICATION_SYSTEM_PROMPT
        user_content = text.strip()

        if last_response:
            user_content = f"{user_content}\n\n[Previous assistant response: {last_response}]"

        if context:
            if context.get("conversationState"):
                conv_state = context["conversationState"]
                active = conv_state.get('active_flow')
                flow_state = conv_state.get('flow_state')
                amount = conv_state.get('amount')
                recipient = conv_state.get('recipient_phone') or conv_state.get('recipient_name')
                
                flow_details = f"Active flow: {active}, State: {flow_state}"
                if amount:
                    flow_details += f", Amount: ₦{amount:,.0f}"
                if recipient:
                    flow_details += f", Recipient: {recipient}"
                
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: {flow_details}]\n"
                    f"CRITICAL: User has a PENDING {active} transaction.\n"
                    f"- If user provides ONLY a new amount (e.g., 'make it 200', 'buy 4k instead', 'I meant 500', '2000'), classify as '{active}' - this UPDATES the pending transaction.\n"
                    f"- If user provides a NEW recipient (different phone/name), classify as '{active}' - this may be changing recipient or starting new.\n"
                    f"- Only classify as a DIFFERENT intent if message CLEARLY asks about something else (balance, accounts, cancel)."
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
        result = await structured_llm.ainvoke(messages)
        
        # Override cancellation for explicit start commands
        # This prevents "Send 50k" being classified as cancel after a previous cancellation
        text_lower = text.lower().strip()
        if any(text_lower.startswith(prefix) for prefix in ["send ", "pay ", "transfer ", "buy "]):
            if result.is_cancellation:
                logger.warning("overriding_false_cancellation", text=text[:30], original_intent=result.intent)
                result.is_cancellation = False
                if result.intent == "cancel":
                    # Fallback to transfer/airtime based on keyword
                    if "airtime" in text_lower or "recharge" in text_lower or "data" in text_lower:
                        result.intent = "airtime" if "airtime" in text_lower else "data"
                    else:
                        result.intent = "transfer"
        
        return result
