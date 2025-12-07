"""Intent classification service for the orchestrator."""

import json
from typing import Any, Optional

from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult


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
        
        # Fast path for greetings - no LLM needed
        greeting_patterns = {
            "hi", "hello", "hey", "hey there", "hi there", "hello there",
            "good morning", "good afternoon", "good evening", "good night",
            "gm", "gn", "morning", "afternoon", "evening",
            # Multilingual greetings
            "bawo", "kaaro", "ekaro",  # Yoruba
            "sannu", "barka",  # Hausa
            "kedu", "ndewo",  # Igbo
            "how far", "how you dey",  # Nigerian Pidgin
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
            amount_match = re.match(r'^[nN]?\s*(\d+)[kK]?$', text_clean)
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
    ) -> ClassificationResult:
        """Classify user intent from text."""
        fast_result = self._try_fast_path(text, context)
        if fast_result:
            print(f"⚡ Fast path: '{text[:50]}' → {fast_result.intent} (confidence: {fast_result.confidence})")
            return fast_result
        


        system = (
            "You are an intent classifier for a banking assistant. "
            "Classify messages into: transfer, airtime, data, query, manage_accounts, conversational, cancel, yes, no, confirm, skip, unknown. "
            "Determine complexity (multi-step reasoning, dynamic amounts, pooling accounts, historical references, multiple transactions). "
            "Detect language (English, Yoruba, Hausa, Igbo, Pidgin, French, etc). Return language name in detected_language field.\n\n"
            
            "**CONVERSATIONAL INTENT (EXPANDED):**\n"
            "- Greetings: hi, hello, good morning, bawo, kedu, sannu\n"
            "- Gratitude: thank you, thanks, eshe, nagode, dalu, you are the best\n"
            "- Jokes/Fun: tell me a joke, say something funny, are you smart?\n"
            "- Identity/Testing: who are you?, are you a bot?, are you real?, you are stupid\n"
            "- Feedback/Comments: good job, this is cool, i like this app\n"
            "- General Banter: simple replies that don't fit other intents but aren't cancellations\n"
            "- If message is purely conversational/social, classify as 'conversational'.\n\n"

            "**QUERY INTENT (FINANCIAL QUESTIONS):**\n"
            "- If a message is asking about transaction history, spending patterns, or financial insights, classify as 'query'\n"
            "- Examples of query intent:\n"
            "  - 'How much did I spend yesterday?' → intent: query\n"
            "  - 'Who did I send money to the most?' → intent: query\n"
            "  - 'Show me all my Uber transactions' → intent: query\n"
            "  - 'Where did my money go this month?' → intent: query\n"
            "  - 'How much did I send to mum?' → intent: query\n"
            "- Query intent is for QUESTIONS about past transactions, not requests to make new transactions\n\n"
            
            "**MANAGE_ACCOUNTS INTENT (ACCOUNT MANAGEMENT):**\n"
            "- If a message is about managing linked bank accounts, classify as 'manage_accounts'\n"
            "- Examples of manage_accounts intent:\n"
            "  - 'Show my accounts' → intent: manage_accounts\n"
            "  - 'List my linked accounts' → intent: manage_accounts\n"
            "  - 'Set GTBank as default' → intent: manage_accounts\n"
            "  - 'Unlink my Access account' → intent: manage_accounts\n"
            "  - 'Link a new account' → intent: manage_accounts\n"
            "  - 'Add another bank account' → intent: manage_accounts\n"
            "  - Numeric responses (1, 2, 3) when in account selection context → intent: manage_accounts\n\n"
            
            "**COMPLEX TRANSACTIONS (MULTIPLE OPERATIONS):**\n"
            "- If a message contains multiple transfers, airtime purchases, or a mix of operations, it is COMPLEX\n"
            "- Examples of complex transactions:\n"
            "  - 'Send 5k to ayo and 20k to mum' → intent: transfer, is_complex: true, complexity_reason: 'multiple transfers'\n"
            "  - 'Transfer 10k to John and buy 5k airtime for mum' → intent: mixed, is_complex: true, complexity_reason: 'multiple operations'\n"
            "  - 'Send 5k to ayo, 10k to mum, and 15k to dad' → intent: transfer, is_complex: true, complexity_reason: 'multiple transfers'\n"
            "- When is_complex=true and complexity_reason contains 'multiple', the system will break it down into separate tasks\n"
            "- CRITICAL: Always set is_complex=true and include 'multiple' in complexity_reason when you see multiple amounts/recipients/operations\n\n"

            "**BENEFICIARY SUGGESTION RESPONSES:**\n"
            "- If context.pendingBeneficiarySuggestion exists and user responds to the suggestion:\n"
            "  - 'yes', 'sure', 'ok', 'confirm', 'save', 'add', 'go ahead', 'proceed' → intent: yes or confirm, extracted_alias: null\n"
            "  - 'no', 'skip', 'don't save', 'not now', 'cancel' → intent: no or skip, extracted_alias: null\n"
            "  - If user provides an alias/name (e.g., 'save as mum', 'My opay', 'mum', 'call it mum', 'save it as mum', or just a name like 'Gaines', 'Mum', 'Home'):\n"
            "    → ALWAYS extract the alias/name and set extracted_alias to that value (just the name, not the full phrase)\n"
            "    → Intent can be 'yes', 'confirm', or keep as original intent\n"
            "    → Examples: 'save as mum' → extracted_alias: 'mum', 'My opay' → extracted_alias: 'My opay', 'mum' → extracted_alias: 'mum', 'Gaines' → extracted_alias: 'Gaines'\n"
            "  - CRITICAL: If previous response asks for a name/alias (e.g., 'Please provide a name or alias'), and user sends just a name, you MUST extract it as extracted_alias\n"
            "  - CRITICAL: When context.pendingBeneficiarySuggestion exists and user provides a single word or short phrase that looks like a name, extract it as extracted_alias\n"
            "  - These are responses to: 'Would you like to save [name] as a beneficiary?' or 'Please provide a name or alias...'\n\n"

            "**CANCELLATION INTENT:**\n"
            "- If user wants to cancel, abort, or stop the current transaction, classify as 'cancel'\n"
            "- Cancellation phrases: 'cancel', 'abort', 'stop', 'nevermind', 'forget it', 'don't send', 'no thanks', 'not now'\n"
            "- Multilingual: 'ma fi sile' (Yoruba: forget it), 'ka soke' (Hausa: stop), equivalent phrases in other languages\n"
            "- Set is_cancellation=true when intent is 'cancel'\n"
            "- If there's an active transaction (context shows active_flow and flow_state not in initial states), cancellation is more likely\n"
            "- If user provides transaction details (amount, account, bank), it's NOT cancellation - it's a continuation\n"
            "- If user wants to change/modify transaction details, it's NOT cancellation - classify as the transaction type\n\n"

            "**CONTEXT AWARENESS (HIGHEST PRIORITY):**\n"
            "- If context.conversationState exists with active_flow='transfer', classify as 'transfer' (continuation) UNLESS user explicitly cancels\n"
            "- If previous assistant response asked for transfer details, and user provides them, classify as 'transfer'\n"
            "- If user says 'cancel' during an active transfer, classify as 'cancel' with is_cancellation=true\n"
            "- Account numbers (10 digits), bank names, or combinations ('0760505261 Access bank') are transfer continuations (not cancellation)\n"
            "- Short responses to transfer questions are continuations\n\n"

            "**EXAMPLES:**\n"
            "- 'cancel' → intent: cancel, is_cancellation: true, is_complex: false\n"
            "- 'fi sile' (Yoruba: forget it) → intent: cancel, is_cancellation: true, is_complex: false\n"
            "- 'stop' → intent: cancel, is_cancellation: true, is_complex: false\n"
            "- 'no thanks' → intent: cancel, is_cancellation: true, is_complex: false\n"
            "- 'send 5k' → intent: transfer, is_cancellation: false, is_complex: false\n"
            "- 'Send 5k to ayo and 20k to mum' → intent: transfer, is_cancellation: false, is_complex: true, complexity_reason: 'multiple transfers'\n"
            "- 'Transfer 10k to John and 15k to Mary' → intent: transfer, is_cancellation: false, is_complex: true, complexity_reason: 'multiple transfers'\n"
            "- 'Access bank' → intent: transfer, is_cancellation: false, is_complex: false\n"
            "- '0760505261 Access bank' → intent: transfer, is_cancellation: false, is_complex: false\n"
            "- '5k' (after being asked for amount) → intent: transfer, is_cancellation: false, is_complex: false\n"
            "- 'change amount to 10k' → intent: transfer, is_cancellation: false, is_complex: false (modification, not cancellation)\n"
            "- 'hi' → intent: conversational, is_cancellation: false, is_complex: false, detected_language: 'English'\n"
            "- 'bawo ni' → intent: conversational, is_cancellation: false, is_complex: false, detected_language: 'Yoruba'\n"
            "- 'thank you' → intent: conversational, is_cancellation: false, is_complex: false\n"
            "- 'tell me a joke' → intent: conversational, is_cancellation: false, is_complex: false\n"
            "- 'check balance' → intent: conversational, is_cancellation: false, is_complex: false\n"
            "- 'yes' (to beneficiary suggestion) → intent: yes or confirm, extracted_alias: null, is_complex: false\n"
            "- 'no' (to beneficiary suggestion) → intent: no or skip, extracted_alias: null, is_complex: false\n" 
            "- 'Gaines' (after being asked 'Please provide a name or alias...') → intent: yes or confirm, extracted_alias: 'Gaines', is_complex: false\n"
            "- 'Mum' (after being asked for alias) → intent: yes or confirm, extracted_alias: 'Mum', is_complex: false\n\n"

            "**PRINCIPLE:** If the message answers a question or provides requested information, it's a continuation. "
            "If the message explicitly cancels/aborts, it's cancellation. "
            "If responding to a yes/no question (like beneficiary suggestion), classify as yes/no/confirm/skip. "
            "Otherwise, classify based on intent.\n"
            "Return ONLY the JSON for the given schema."
        )


        user_content = text.strip()

        if last_response:
            user_content = f"{user_content}\n\n[Previous assistant response: {last_response}]"

        if context:
            if context.get("conversationState"):
                conv_state = context["conversationState"]
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: Active flow: {conv_state.get('active_flow')}, "
                    f"Flow state: {conv_state.get('flow_state')}]\n"
                    f"This message is likely providing information for the ongoing {conv_state.get('active_flow')} flow."
                )

            if context.get("pendingBeneficiarySuggestion"):
                suggestion = context["pendingBeneficiarySuggestion"]
                recipient_name = suggestion.get(
                    "recipient_name", "this recipient")
                beneficiary_type = suggestion.get("beneficiary_type", "transfer")
                
                # Use the actual last_response to understand what was asked
                context_message = f"The assistant just asked about saving a beneficiary: {recipient_name}."
                if last_response:
                    # Include the actual last response to help classifier understand the context
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

        raw = await self.classifier_llm.ainvoke(
            [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
        )
        
        # Handle different return types from LLM
        if isinstance(raw, ClassificationResult):
            return raw
        
        # Extract content from AIMessage if needed
        if isinstance(raw, AIMessage):
            content = raw.content
        else:
            content = raw
        
        # Parse JSON string if needed
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                # If it's not valid JSON, try to extract JSON from the string
                # Some LLMs return JSON wrapped in markdown code blocks
                import re
                json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                if json_match:
                    content = json.loads(json_match.group())
                else:
                    raise ValueError(f"Could not parse JSON from LLM response: {content}")
        
        # Ensure all required fields are present with defaults
        if isinstance(content, dict):
            # Provide defaults for missing required fields
            defaults = {
                "response": "",
                "is_complex": False,
                "complexity_reason": "",
                "confidence": 0.0,
            }
            # Only add defaults for fields that are missing
            for key, default_value in defaults.items():
                if key not in content:
                    content[key] = default_value
        
        return ClassificationResult.model_validate(content)
