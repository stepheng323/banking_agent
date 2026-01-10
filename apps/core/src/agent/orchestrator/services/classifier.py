"""Intent classification service for the orchestrator."""

import re
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.orchestrator.prompts.classification import (
    CLASSIFICATION_SYSTEM_PROMPT,
)
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.services.fast_path import (
    GREETING_PATTERNS,
    CANCEL_PATTERNS,
    CONFIRM_PATTERNS,
    DECLINE_PATTERNS,
    MANAGE_ACCOUNT_KEYWORDS,
    MANAGE_ACCOUNT_PHRASES,
    QUERY_PATTERNS,
    ACCOUNT_NUMBER_PATTERN,
    PHONE_NUMBER_PATTERN,
    is_complex_request,
    match_transfer,
    match_airtime,
    match_data,
)
from apps.core.src.agent.tools.account_selection.mandate_validator import validate_mandate_status
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorClassificationService:
    """Handles intent classification using LLM."""

    def __init__(self, classifier_llm: Runnable) -> None:
        self.classifier_llm = classifier_llm

    def _generate_greeting_response(self, context: dict[str, Any] | None) -> str:
        """Generate mandate-aware greeting response."""
        if not context:
            return "Hi! 👋 How can I help you today?"

        user_ctx = context.get("userContext", {})
        profile = user_ctx.get("profile", {})
        accounts = user_ctx.get("accounts", [])

        name = ""
        if isinstance(profile, dict):
            name = profile.get("full_name") or profile.get("first_name") or ""
        
        greeting = f"Hi{' ' + name.split()[0] if name else ''}! 👋"

        if not accounts:
            return f"{greeting} Your account setup is incomplete. Please complete onboarding to start using our services."
        
        for account in accounts:
            is_valid, _, _ = validate_mandate_status(account)
            if is_valid:
                return f"{greeting} I can help with transfers, airtime, data purchases, and more. What would you like to do?"

        default_account = next(
            (acc for acc in accounts if acc.get("is_default")), 
            accounts[0]
        )
        _, error_message, _ = validate_mandate_status(default_account)
        
        if error_message:
            return f"{greeting}\n\n{error_message}"
        
        return f"{greeting} Your account is being set up. We'll notify you when it's ready!"

    def _try_fast_path(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> ClassificationResult | None:
        """
        Try to classify using fast path when intent is unambiguous.
        Returns None if LLM classification is needed.
        """
        text_clean = text.strip().lower()

        # Reject complex requests
        if is_complex_request(text_clean):
            return None

        # Greetings
        if text_clean in GREETING_PATTERNS:
            return ClassificationResult(
                intent="conversational",
                is_cancellation=False,
                is_complex=False,
                confidence=0.99,
                response=self._generate_greeting_response(context),
                complexity_reason="Simple greeting",
            )

        # Get active flow context
        active_flow = None
        if context and context.get("conversationState"):
            active_flow = context["conversationState"].get("active_flow")

        # Cancel patterns
        if text_clean in CANCEL_PATTERNS:
            if active_flow:
                return None  # Let LLM handle in-flow cancellation
            return ClassificationResult(
                intent="cancel",
                is_cancellation=True,
                is_complex=False,
                confidence=0.99,
                response="There's nothing to cancel. How can I help?",
                complexity_reason="Simple single intent",
            )

        # Yes/No without active flow
        if not active_flow:
            if text_clean in CONFIRM_PATTERNS:
                return ClassificationResult(
                    intent="yes",
                    is_complex=False,
                    confidence=0.95,
                    response="Confirmed.",
                    complexity_reason="Simple single intent",
                )
            if text_clean in DECLINE_PATTERNS:
                return ClassificationResult(
                    intent="no",
                    is_complex=False,
                    confidence=0.95,
                    response="Declined.",
                    complexity_reason="Simple single intent",
                )

        # Flow continuation patterns
        if active_flow in {"transfer", "airtime", "data"}:
            words = set(text_clean.split())

            # Account management during flow
            is_manage = bool(words & MANAGE_ACCOUNT_KEYWORDS) or any(
                phrase in text_clean for phrase in MANAGE_ACCOUNT_PHRASES
            )
            if is_manage:
                return ClassificationResult(
                    intent="manage_accounts",
                    is_complex=False,
                    confidence=0.95,
                    response="",
                    complexity_reason="Account management during active flow",
                )

            # Query during flow
            if words & QUERY_PATTERNS:
                return ClassificationResult(
                    intent="query",
                    is_complex=False,
                    confidence=0.95,
                    response="",
                    complexity_reason="Query during active flow",
                )

            # Account number in transfer flow
            if active_flow == "transfer" and ACCOUNT_NUMBER_PATTERN.match(text_clean):
                return ClassificationResult(
                    intent="transfer",
                    is_complex=False,
                    confidence=0.97,
                    response="",
                    complexity_reason="Account number detected",
                )

            # Phone number in airtime/data flow
            if active_flow in {"airtime", "data"} and PHONE_NUMBER_PATTERN.match(text_clean):
                return ClassificationResult(
                    intent=active_flow,
                    is_complex=False,
                    confidence=0.97,
                    response="",
                    complexity_reason="Phone number detected",
                )

            return None

        # Multi-intent check before transaction patterns
        if " and " in text_clean and any(kw in text_clean for kw in ["send", "transfer", "pay"]):
            return None

        # Transaction patterns (via fast_path module)
        result = match_transfer(text_clean)
        if result:
            return result

        result = match_airtime(text_clean)
        if result:
            return result

        result = match_data(text_clean)
        if result:
            return result

        return None

    async def classify(
        self,
        text: str,
        context: dict[str, Any] | None = None,
        last_response: str | None = None,
        image_data: str | None = None,
    ) -> ClassificationResult:
        """Classify user intent from text."""
        conv_state = context.get("conversationState") if context else None
        if conv_state:
            logger.info(
                "classification_with_active_flow",
                active_flow=conv_state.get("active_flow"),
                flow_state=conv_state.get("flow_state"),
                text=text[:50],
            )
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
                active = conv_state.get("active_flow")
                flow_state = conv_state.get("flow_state")
                amount = conv_state.get("amount")
                recipient = conv_state.get("recipient_phone") or conv_state.get("recipient_name")

                flow_details = f"Active flow: {active}, State: {flow_state}"
                if amount:
                    flow_details += f", Amount: ₦{amount:,.0f}"
                if recipient:
                    flow_details += f", Recipient: {recipient}"

                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: {flow_details}]\n"
                    f"CRITICAL: User has a PENDING {active} transaction.\n"
                    f"- If user provides ONLY a new amount (e.g., 'make it 200', 'buy 4k instead',\n"
                    f"'I meant 500', '2000'), classify as '{active}' - this UPDATES the pending transaction.\n"
                    f"- If user provides a NEW recipient (different phone/name), classify as '{active}'\n"
                    f"- this may be changing recipient or starting new.\n"
                    f"- Only classify as a DIFFERENT intent if message CLEARLY asks about something else\n"
                    f"- (balance, accounts, cancel)."
                )

            if context.get("pendingBeneficiarySuggestion"):
                suggestion = context["pendingBeneficiarySuggestion"]
                recipient_name = suggestion.get("recipient_name", "this recipient")
                beneficiary_type = suggestion.get("beneficiary_type", "transfer")

                context_message = f"The assistant just asked about saving a beneficiary: {recipient_name}."
                if last_response:
                    context_message = f"The assistant's last message was: '{last_response}'"

                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: {context_message}]\n"
                    f"Beneficiary type: {beneficiary_type}. "
                    f"This message is a response to that question. "
                    f"CRITICAL: If the last message asked for a name/alias and user provides just a name /n"
                    f"(even a single word like 'Gaines'), you MUST extract it as extracted_alias. /n"
                    f"Classify as 'yes'/'confirm' if user wants to save, 'no'/'skip' if user declines, "
                    f"or extract the alias if user provides a name. When in doubt and user provides a name-like word,"
                    f"extract it as extracted_alias."
                )

            if context.get("quotedMessage"):
                quoted = context["quotedMessage"]
                quoted_type = quoted.get("type", "unknown")
                quoted_data = quoted.get("data", {})

                if quoted_type == "transfer_success":
                    amount = quoted_data.get("amount", 0)
                    recipient = quoted_data.get("recipient_name", "unknown")
                    quoted_summary = f"Transfer of ₦{amount:,.0f} to {recipient}"
                elif quoted_type == "airtime_success":
                    amount = quoted_data.get("amount", 0)
                    phone = quoted_data.get("phone_number", "unknown")
                    quoted_summary = f"Airtime of ₦{amount:,.0f} to {phone}"
                elif quoted_type == "transfer_confirmation":
                    amount = quoted_data.get("amount", 0)
                    recipient = quoted_data.get("recipient_name", "unknown")
                    quoted_summary = f"Pending transfer confirmation for ₦{amount:,.0f} to {recipient}"
                elif quoted_type == "airtime_confirmation":
                    amount = quoted_data.get("amount", 0)
                    phone = quoted_data.get("phone_number", "unknown")
                    quoted_summary = f"Pending airtime confirmation for ₦{amount:,.0f} to {phone}"
                else:
                    quoted_summary = f"Message of type '{quoted_type}'"

                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: User is QUOTING a previous message - {quoted_summary}]\n"
                    f"CRITICAL: User is replying to a {quoted_type} message. "
                    f"If user says 'again', 'repeat', 'same', 'yes', '👍', 'do it' → intent: repeat_transaction. "
                    f"If user mentions a different amount like '5k',\n"
                    f"'10k' → intent: modify_transaction, extract new_amount."
                )

            if context.get("quotedMessageNotFound"):
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: User is QUOTING a message but we could NOT find transaction details\n"
                    f"- it may be expired or not a transaction]\n"
                    f"CRITICAL: Generate a helpful response explaining we can't repeat that transaction. "
                    f"Suggest they start a new one. Example: 'I couldn't find that transaction details.\n"
                    f'It may be too old. Want to start a new transfer? Just say "send 5k to Mum"\''
                )

        messages = [{"role": "system", "content": system}]

        if image_data:
            user_message = {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": image_data}},
                ],
            }
        else:
            user_message = {"role": "user", "content": user_content}

        messages.append(user_message)

        structured_llm = self.classifier_llm.with_structured_output(ClassificationResult)
        result = await structured_llm.ainvoke(messages)

        text_lower = text.lower().strip()
        if any(text_lower.startswith(prefix) for prefix in ["send ", "pay ", "transfer ", "buy "]):
            if result.is_cancellation:
                logger.warning("overriding_false_cancellation", text=text[:30], original_intent=result.intent)
                result.is_cancellation = False
                if result.intent == "cancel":
                    if "airtime" in text_lower or "recharge" in text_lower or "data" in text_lower:
                        result.intent = "airtime" if "airtime" in text_lower else "data"
                    else:
                        result.intent = "transfer"

        return result
