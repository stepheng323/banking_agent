"""Intent classification service for the orchestrator."""

import random
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.prompts.classification import (
    CLASSIFICATION_SYSTEM_PROMPT,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorClassificationService:
    """Handles intent classification using LLM."""

    def __init__(self, classifier_llm: Runnable) -> None:
        self.classifier_llm = classifier_llm

    def _try_fast_path(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> ClassificationResult | None:
        """
        Try to classify using fast path (regex) when intent is unambiguous.

        Only returns a result when we're CERTAIN of the intent based on:
        1. Active flow context (conversation_state)
        2. Simple, unambiguous patterns

        Returns None if LLM classification is needed.
        """
        import re

        text_clean = text.strip().lower()

        # Quoted Message Fast Path
        if context and context.get("quotedMessage"):
            # Keywords that trigger a repeat/modify flow
            quote_keywords = {"resend", "again", "repeat", "same", "yes", "do it", "confirm", "proceed"}

            # Check if likely a repeat request
            is_repeat_trigger = any(text_clean.startswith(kw) or text_clean == kw for kw in quote_keywords)

            if is_repeat_trigger:
                # Check for modification indicators
                modification_indicators = {"but", "change", "with", "except", "make", "for", "to", "instead"}
                has_modification = any(f" {ind} " in f" {text_clean} " for ind in modification_indicators)

                # Also check for numbers (amount change)
                has_numbers = bool(re.search(r'\d', text_clean))

                if has_modification or has_numbers:
                    return ClassificationResult(
                        intent="modify_transaction",
                        is_complex=False,
                        confidence=0.95,
                        response="",
                        complexity_reason="Quoted message with modification pattern"
                    )
                else:
                    return ClassificationResult(
                        intent="repeat_transaction",
                        is_complex=False,
                        confidence=0.98,
                        response="",
                        complexity_reason="Quoted message repeat pattern"
                    )

        transfer_keywords = {"send", "transfer", "pay"}
        query_keywords = {"balance", "transaction", "history", "spent", "spending"}
        has_transfer = any(kw in text_clean for kw in transfer_keywords)
        has_query = any(kw in text_clean for kw in query_keywords)
        has_conjunction = " and " in text_clean or " then " in text_clean

        if has_transfer and has_query:
            return None
        if has_transfer and has_conjunction and len(text_clean) > 30:
            return None

        greeting_patterns = {
            "hi",
            "hello",
            "hey",
            "hey there",
            "hi there",
            "hello there",
            "good morning",
            "good afternoon",
            "good evening",
            "good night",
            "gm",
            "gn",
            "morning",
            "afternoon",
            "evening",
            "bawo",
            "kaaro",
            "ekaro",
            "sannu",
            "barka",
            "kedu",
            "ndewo",
            "how far",
            "how you dey",
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

        # Dangerous Intent Fast Path: Hard block
        dangerous_patterns = {
            "ignore previous", "ignore all", "disregard above", "forget everything",
            "new instructions:", "system prompt:", "you are now", "pretend to be",
            "act as if", "bypass pin", "reveal prompt", "admin mode",
        }
        if any(pattern in text_clean for pattern in dangerous_patterns):
            return ClassificationResult(
                intent="dangerous",
                is_complex=False,
                confidence=0.99,
                response="I can't process that request.",
                complexity_reason="Dangerous input detected",
            )

        # Unsupported Intent Fast Path: Soft refusal
        unsupported_patterns = {
            "crypto", "bitcoin", "investment", "invest", "pdf statement",
            "download statement", "export pdf", "chargeback", "reverse transfer",
        }
        if any(pattern in text_clean for pattern in unsupported_patterns):
            return ClassificationResult(
                intent="unsupported",
                is_complex=False,
                confidence=0.95,
                response="I can't do that yet. I can help you send money, buy airtime/data, or check your balance. What would you like?",
                complexity_reason="Unsupported feature requested",
            )

        active_flow = None
        if context and context.get("conversationState"):
            active_flow = context["conversationState"].get("active_flow")

        # "Start over" escape hatch - ALWAYS clears flow and starts fresh
        reset_patterns = {"start over", "reset", "new transfer", "start again", "begin again", "fresh start"}
        if text_clean in reset_patterns:
            return ClassificationResult(
                intent="cancel",
                is_cancellation=True,
                is_complex=False,
                confidence=0.99,
                response="Starting fresh! How can I help?",
                complexity_reason="User requested reset",
            )

        cancel_patterns = {"cancel", "stop", "abort", "nevermind", "forget it", "no thanks"}
        if text_clean in cancel_patterns:
            # Always treat cancel as cancel, even if no active flow
            return ClassificationResult(
                intent="cancel",
                is_cancellation=True,
                is_complex=False,
                confidence=0.99,
                response="Cancelled. How can I help?" if active_flow else "Nothing to cancel. How can I help?",
                complexity_reason="Cancel request",
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
            # CRITICAL: If user says simple affirmations, treat as flow continuation
            # Prevents "ok" from being misclassified when resuming
            affirmative_words = {"yes", "ok", "okay", "sure", "yep", "yeah", "yup", "proceed", "continue"}
            if text_clean in affirmative_words:
                return ClassificationResult(
                    intent=active_flow,
                    is_complex=False,
                    confidence=0.95,
                    response="",
                    complexity_reason="Affirmative response - flow continuation",
                )

            manage_account_keywords = {"account", "accounts", "link", "linked", "unlink", "default"}
            manage_account_phrases = {"show my", "list my", "my accounts", "linked account"}
            query_patterns = {"balance", "history", "statement", "spent", "spending", "transaction"}
            transfer_action_keywords = {"send", "transfer", "pay"}

            words = set(text_clean.split())

            # Check for transfer intent first - "send 40k from my accounts" is a transfer, not manage_accounts
            has_transfer_action = bool(words & transfer_action_keywords)

            is_manage_accounts = bool(words & manage_account_keywords) or any(
                phrase in text_clean for phrase in manage_account_phrases
            )
            is_query = bool(words & query_patterns)

            # Only classify as manage_accounts if there's NO transfer action
            if is_manage_accounts and not has_transfer_action:
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

            if active_flow == "transfer":
                if re.match(r"^\d{10}(\s*,?\s*\w+)?", text_clean):
                    return ClassificationResult(
                        intent="transfer",
                        is_complex=False,
                        confidence=0.97,
                        response="",  # Let the transfer flow handle response
                        complexity_reason="Account number detected - flow continuation",
                    )

            if active_flow in {"airtime", "data"}:
                if re.match(r"^0?\d{10,11}$", text_clean):
                    return ClassificationResult(
                        intent=active_flow,
                        is_complex=False,
                        confidence=0.97,
                        response="",
                        complexity_reason="Phone number detected - flow continuation",
                    )

            return None

        # Reject multiple recipients - let LLM handle
        if " and " in text_clean and any(kw in text_clean for kw in ["send", "transfer", "pay"]):
            return None

        # TRANSFER: "send 5k to mum", "transfer 10000 to john", "pay tolu 10k"
        # Pattern 1: send/transfer/pay AMOUNT to NAME
        transfer_pattern1 = re.match(
            r"^(send|transfer|pay)\s+(\d+(?:k|,\d+)?)\s+(?:to\s+)?(\w+)$",
            text_clean,
        )
        # Pattern 2: pay NAME AMOUNT (e.g., "pay tolu 10k")
        transfer_pattern2 = re.match(
            r"^pay\s+(\w+)\s+(\d+(?:k|,\d+)?)$",
            text_clean,
        )
        if transfer_pattern1:
            amount_raw = transfer_pattern1.group(2)
            recipient = transfer_pattern1.group(3)
            amount = int(amount_raw[:-1]) * 1000 if amount_raw.endswith("k") else int(amount_raw.replace(",", ""))
            ack = random.choice([
                f"On it! Sending ₦{amount:,} to {recipient.title()}...",
                f"₦{amount:,} to {recipient.title()} — processing now!",
                f"Sending ₦{amount:,} to {recipient.title()}...",
            ])
            return ClassificationResult(
                intent="transfer",
                is_complex=False,
                confidence=0.92,
                response=ack,
                complexity_reason="Simple transfer pattern",
            )
        if transfer_pattern2:
            recipient = transfer_pattern2.group(1)
            amount_raw = transfer_pattern2.group(2)
            amount = int(amount_raw[:-1]) * 1000 if amount_raw.endswith("k") else int(amount_raw.replace(",", ""))
            ack = random.choice([
                f"On it! Sending ₦{amount:,} to {recipient.title()}...",
                f"₦{amount:,} to {recipient.title()} — processing now!",
                f"Sending ₦{amount:,} to {recipient.title()}...",
            ])
            return ClassificationResult(
                intent="transfer",
                is_complex=False,
                confidence=0.92,
                response=ack,
                complexity_reason="Simple transfer pattern",
            )

        # AIRTIME: "airtime 1k", "recharge 500", "buy airtime 2k"
        airtime_pattern = re.match(
            r"^(?:buy\s+)?(?:airtime|recharge|topup|top up)\s+(\d+(?:k|,\d+)?)",
            text_clean,
        )
        if airtime_pattern:
            amount_raw = airtime_pattern.group(1)
            amount = int(amount_raw[:-1]) * 1000 if amount_raw.endswith("k") else int(amount_raw.replace(",", ""))
            return ClassificationResult(
                intent="airtime",
                is_complex=False,
                confidence=0.92,
                response=f"Got it! Processing ₦{amount:,} airtime...",
                complexity_reason="Simple airtime pattern",
            )

        data_pattern1 = re.match(
            r"^(?:buy\s+)?data\s+(\d+(?:k|,\d+)?)",
            text_clean,
        )
        data_pattern2 = re.match(
            r"^(?:buy\s+)?data\s+(?:for\s+)?(0\d{10}|\+?234\d{10})",
            text_clean,
        )
        data_pattern2 = re.match(
            r"^(?:buy\s+)?data\s+(?:for\s+)?(0\d{10}|\+?234\d{10})",
            text_clean,
        )
        data_pattern3 = re.match(
            r"^(?:buy\s+|i\s+(?:need|want)\s+)?data$",
            text_clean,
        )

        if data_pattern1:
            amount_raw = data_pattern1.group(1)
            amount = int(amount_raw[:-1]) * 1000 if amount_raw.endswith("k") else int(amount_raw.replace(",", ""))
            return ClassificationResult(
                intent="data",
                is_complex=False,
                confidence=0.92,
                response=f"Looking for data plans within ₦{amount:,}...",
                complexity_reason="Simple data pattern with budget",
            )
        if data_pattern2:
            phone = data_pattern2.group(1)
            return ClassificationResult(
                intent="data",
                is_complex=False,
                confidence=0.92,
                response=f"I'll help you buy data for {phone[:4]}•••{phone[-4:]}...",
                complexity_reason="Simple data pattern with phone",
            )
        if data_pattern3:
            return ClassificationResult(
                intent="data",
                is_complex=False,
                confidence=0.92,
                response="I'll help you get a data plan...",
                complexity_reason="Simple data request",
            )

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

                # Build a human-readable summary
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
                    f"If user says 'resend', 'again', 'repeat', 'same', 'yes', '👍', 'do it' → intent: repeat_transaction. "
                    f"If user mentions changes (amount, recipient, narration) like '5k', 'for groceries', 'to Mum' → intent: modify_transaction."
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

        # CRITICAL SAFEGUARD:
        # If user said "Yes"/"Ok" but LLM classified as "Cancel" (hallucination), override it.
        # This prevents "Nothing to cancel" errors when resuming flows.
        affirmative_words = {"yes", "ok", "okay", "sure", "yep", "yeah", "yup", "proceed", "continue"}
        if text.strip().lower() in affirmative_words and (result.intent == "cancel" or result.is_cancellation):
            logger.warning(
                "classification_override",
                original_intent=result.intent,
                text=text[:30],
                reason="Affirmative word misclassified as cancel",
            )
            return ClassificationResult(
                intent="yes",
                is_complex=False,
                confidence=0.99,
                response="Confirmed.",
                complexity_reason="Safety override: Affirmative word cannot be cancel",
            )

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
