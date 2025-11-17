"""Intent classification service for the orchestrator."""

from typing import Any, Optional

from langchain_core.runnables import Runnable
from apps.core.src.agent.models.classification import ClassificationResult


class OrchestratorClassificationService:
    """Handles intent classification using LLM."""

    def __init__(self, classifier_llm: Runnable) -> None:
        self.classifier_llm = classifier_llm

    async def classify(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
        last_response: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify user intent from text."""
        system = (
            "You are an intent classifier for a banking assistant. "
            "Classify messages into: transfer, airtime, data, conversational, cancel, yes, no, confirm, skip, unknown. "
            "Determine complexity (multi-step reasoning, dynamic amounts, pooling accounts, historical references, multiple transactions). "
            "Work across languages: English, Yoruba, Hausa, Igbo, Nigerian Pidgin, French, and more.\n\n"

            "**BENEFICIARY SUGGESTION RESPONSES:**\n"
            "- If context.pendingBeneficiarySuggestion exists and user responds to the suggestion:\n"
            "  - 'yes', 'sure', 'ok', 'confirm', 'save', 'add', 'go ahead', 'proceed' → intent: yes or confirm, extracted_alias: null\n"
            "  - 'no', 'skip', 'don't save', 'not now', 'cancel' → intent: no or skip, extracted_alias: null\n"
            "  - If user provides an alias/name (e.g., 'save as mum', 'My opay', 'mum', 'call it mum', 'save it as mum'):\n"
            "    → Extract the alias/name and set extracted_alias to that value (just the name, not the full phrase)\n"
            "    → Intent can be 'yes', 'confirm', or keep as original intent\n"
            "    → Examples: 'save as mum' → extracted_alias: 'mum', 'My opay' → extracted_alias: 'My opay', 'mum' → extracted_alias: 'mum'\n"
            "  - These are responses to: 'Would you like to save [name] as a beneficiary?'\n\n"

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
            "- 'cancel' → intent: cancel, is_cancellation: true\n"
            "- 'fi sile' (Yoruba: forget it) → intent: cancel, is_cancellation: true\n"
            "- 'stop' → intent: cancel, is_cancellation: true\n"
            "- 'no thanks' → intent: cancel, is_cancellation: true\n"
            "- 'send 5k' → intent: transfer, is_cancellation: false\n"
            "- 'Access bank' → intent: transfer, is_cancellation: false\n"
            "- '0760505261 Access bank' → intent: transfer, is_cancellation: false\n"
            "- '5k' (after being asked for amount) → intent: transfer, is_cancellation: false\n"
            "- 'change amount to 10k' → intent: transfer, is_cancellation: false (modification, not cancellation)\n"
            "- 'hi' → intent: conversational, is_cancellation: false\n"
            "- 'check balance' → intent: conversational, is_cancellation: false\n"
            "- 'yes' (to beneficiary suggestion) → intent: yes or confirm\n"
            "- 'no' (to beneficiary suggestion) → intent: no or skip\n\n"

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
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: The assistant just asked: 'Would you like to save {recipient_name} as a beneficiary for faster transfers? Reply to confirm.']\n"
                    f"This message is a response to that question. Classify as 'yes'/'confirm' if user wants to save, or 'no'/'skip' if user declines."
                )

        raw = await self.classifier_llm.ainvoke(
            [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
        )
        if isinstance(raw, ClassificationResult):
            return raw
        return ClassificationResult.model_validate(raw)
