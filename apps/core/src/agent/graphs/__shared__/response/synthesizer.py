"""Response Synthesizer - Unified response generation for all subgraphs."""

import re

from langchain_core.runnables import Runnable

from shared.i18n import MessageKey, render_message
from shared.i18n.message_keys import as_message_key
from shared.utils.logging import get_logger

from .context import ResponseContext
from .intent import ResponseIntent
from .templates import get_template

logger = get_logger(__name__)


class ResponseSynthesizer:
    """Unified response generator with template and LLM modes.

    Template mode (fast): Uses predefined templates for common intents
    LLM mode (natural): Falls back to LLM for complex/personalized responses
    """

    def __init__(self, llm: Runnable | None = None):
        """Initialize response synthesizer.

        Args:
            llm: Optional LLM for complex response generation
        """
        self.llm = llm

    async def synthesize(self, context: ResponseContext) -> str:
        """Generate response from context.

        Args:
            context: ResponseContext with intent and data

        Returns:
            Natural language response string
        """
        template = get_template(context.intent, context.language, context.recipient_name)
        if template:
            try:
                response = self._render_template(template, context)
                logger.debug(
                    "response_synthesized_template",
                    intent=context.intent.value,
                    language=context.language,
                )
                return response
            except Exception as e:
                logger.warning("template_render_failed", intent=context.intent.value, error=str(e))

        if self.llm:
            try:
                response = await self._llm_synthesize(context)
                logger.debug("response_synthesized_llm", intent=context.intent.value)
                return response
            except Exception as e:
                logger.error("llm_synthesis_failed", intent=context.intent.value, error=str(e))

        return self._get_fallback(context)

    def _render_template(self, template: str, context: ResponseContext) -> str:
        """Render template with context variables.

        Handles missing variables gracefully.
        """
        variables = self._build_template_variables(context)

        if template.startswith("response."):
            return render_message(as_message_key(template), context.language, variables)

        # Legacy literal-template path (kept for compatibility during migration).
        def replace_var(match):
            var_name = match.group(1)
            value = variables.get(var_name, "")
            return str(value) if value is not None else ""

        return re.sub(r"\{(\w+)\}", replace_var, template)

    def _build_template_variables(self, context: ResponseContext) -> dict[str, object]:
        """Build interpolation variables for deterministic template rendering."""
        variables: dict[str, object] = {
            "user_name": context.user_name or "",
            "user_greeting": f" {context.user_name}" if context.user_name else "",
            "amount": context.amount,
            "formatted_amount": context.format_amount(),
            "recipient_name": context.recipient_name
            or render_message("response.common.recipient_fallback", context.language),
            "recipient_account": context.recipient_account or "",
            "recipient_account_masked": context.recipient_account_masked
            or context.mask_account(context.recipient_account),
            "bank_name": context.bank_name or "",
            "phone_number": context.phone_number or "",
            "phone_masked": context.phone_masked or context.mask_phone(context.phone_number),
            "network": context.network or "",
            "data_plan": context.data_plan or "",
            "source_account_name": context.source_account_name or "",
            "source_bank_name": context.source_bank_name or "",
            "balance": context.balance,
            "error_message": context.error_message
            or render_message("response.common.error_occurred", context.language),
            "transaction_id": context.transaction_id or "",
            "transaction_reference": context.transaction_reference or "",
        }

        if context.candidates:
            candidates_list = "\n".join(
                [
                    render_message(
                        "response.format.candidate_item",
                        context.language,
                        {
                            "name": c.get("name")
                            or c.get("account_name")
                            or render_message("response.common.unknown", context.language),
                            "bank_name": c.get("bank_name")
                            or render_message("response.common.na", context.language),
                            "last4": str(c.get("account_number", ""))[-4:],
                        },
                    )
                    for c in context.candidates
                ]
            )
            variables["candidates_list"] = candidates_list
        else:
            variables["candidates_list"] = ""

        variables.update(context.extra)
        return variables

    async def _llm_synthesize(self, context: ResponseContext) -> str:
        """Generate response using LLM."""
        if not self.llm:
            return self._get_fallback(context)

        system_prompt = (
            "You are a helpful banking assistant. Generate a natural, friendly response "
            "based on the intent and context provided. Keep responses concise and conversational.\n\n"
            "Rules:\n"
            "- Be warm but professional\n"
            "- Use Nigerian English style when appropriate\n"
            "- Format currency as ₦X,XXX\n"
            "- Keep responses under 2 sentences when possible\n"
        )

        user_prompt = f"""Intent: {context.intent.value}
Context:
- User name: {context.user_name or "Unknown"}
- Amount: {context.format_amount() if context.amount else "Not specified"}
- Recipient: {context.recipient_name or "Not specified"}
- Bank: {context.bank_name or "Not specified"}
- Error: {context.error_message or "None"}

Generate a natural response for this intent."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self.llm.ainvoke(messages)

        if hasattr(response, "content"):
            return response.content
        return str(response)

    def _get_fallback(self, context: ResponseContext) -> str:
        """Get fallback response for unknown intents."""
        fallback_keys: dict[ResponseIntent, MessageKey] = {
            ResponseIntent.ASK_AMOUNT: "response.templates.ask_amount",
            ResponseIntent.ASK_RECIPIENT: "response.templates.ask_recipient_no_name",
            ResponseIntent.ASK_BANK: "response.templates.ask_bank",
            ResponseIntent.CANCELLED: "response.templates.cancelled",
            ResponseIntent.TRANSFER_FAILED: "response.templates.transfer_failed",
        }

        template_key = fallback_keys.get(context.intent)
        if template_key:
            return render_message(template_key, context.language, self._build_template_variables(context))

        return render_message(
            "response.fallback.generic",
            context.language,
        )


_synthesizer: ResponseSynthesizer | None = None


def get_synthesizer(llm: Runnable | None = None) -> ResponseSynthesizer:
    """Get or create singleton ResponseSynthesizer instance."""
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ResponseSynthesizer(llm=llm)
    elif llm and not _synthesizer.llm:
        _synthesizer.llm = llm
    return _synthesizer
