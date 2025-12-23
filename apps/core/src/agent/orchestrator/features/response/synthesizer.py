"""Response Synthesizer - Unified response generation for all subgraphs."""

import re
from typing import Optional

from langchain_core.runnables import Runnable

from shared.utils.logging import get_logger

from .intent import ResponseIntent
from .context import ResponseContext
from .templates import get_template

logger = get_logger(__name__)


class ResponseSynthesizer:
    """Unified response generator with template and LLM modes.
    
    Template mode (fast): Uses predefined templates for common intents
    LLM mode (natural): Falls back to LLM for complex/personalized responses
    """
    
    def __init__(self, llm: Optional[Runnable] = None):
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
                    language=context.language
                )
                return response
            except Exception as e:
                logger.warning(
                    "template_render_failed",
                    intent=context.intent.value,
                    error=str(e)
                )
        
        if self.llm:
            try:
                response = await self._llm_synthesize(context)
                logger.debug(
                    "response_synthesized_llm",
                    intent=context.intent.value
                )
                return response
            except Exception as e:
                logger.error(
                    "llm_synthesis_failed",
                    intent=context.intent.value,
                    error=str(e)
                )
        
        return self._get_fallback(context)
    
    def _render_template(self, template: str, context: ResponseContext) -> str:
        """Render template with context variables.
        
        Handles missing variables gracefully.
        """
        variables = {
            "user_name": context.user_name or "",
            "user_greeting": f" {context.user_name}" if context.user_name else "",
            "amount": context.amount,
            "formatted_amount": context.format_amount(),
            "recipient_name": context.recipient_name or "recipient",
            "recipient_account": context.recipient_account or "",
            "recipient_account_masked": context.recipient_account_masked or context.mask_account(context.recipient_account),
            "bank_name": context.bank_name or "",
            "phone_number": context.phone_number or "",
            "phone_masked": context.phone_masked or context.mask_phone(context.phone_number),
            "network": context.network or "",
            "data_plan": context.data_plan or "",
            "source_account_name": context.source_account_name or "",
            "source_bank_name": context.source_bank_name or "",
            "balance": context.balance,
            "error_message": context.error_message or "An error occurred",
            "transaction_id": context.transaction_id or "",
            "transaction_reference": context.transaction_reference or "",
        }
        
        if context.candidates:
            candidates_list = "\n".join([
                f"• {c.get('name', c.get('account_name', 'Unknown'))} ({c.get('bank_name', 'N/A')} • …{str(c.get('account_number', ''))[-4:]})"
                for c in context.candidates
            ])
            variables["candidates_list"] = candidates_list
        else:
            variables["candidates_list"] = ""
        
        variables.update(context.extra)
        
        def replace_var(match):
            var_name = match.group(1)
            value = variables.get(var_name, "")
            return str(value) if value is not None else ""
        
        return re.sub(r'\{(\w+)\}', replace_var, template)
    
    async def _llm_synthesize(self, context: ResponseContext) -> str:
        """Generate response using LLM."""
        if not self.llm:
            return self._get_fallback(context)
        
        system_prompt = """You are a helpful banking assistant. Generate a natural, friendly response based on the intent and context provided. Keep responses concise and conversational.

Rules:
- Be warm but professional
- Use Nigerian English style when appropriate
- Format currency as ₦X,XXX
- Keep responses under 2 sentences when possible
"""
        
        user_prompt = f"""Intent: {context.intent.value}
Context:
- User name: {context.user_name or 'Unknown'}
- Amount: {context.format_amount() if context.amount else 'Not specified'}
- Recipient: {context.recipient_name or 'Not specified'}
- Bank: {context.bank_name or 'Not specified'}
- Error: {context.error_message or 'None'}

Generate a natural response for this intent."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = await self.llm.ainvoke(messages)
        
        if hasattr(response, 'content'):
            return response.content
        return str(response)
    
    def _get_fallback(self, context: ResponseContext) -> str:
        """Get fallback response for unknown intents."""
        fallbacks = {
            ResponseIntent.ASK_AMOUNT: "How much would you like to send?",
            ResponseIntent.ASK_RECIPIENT: "Who would you like to send to?",
            ResponseIntent.ASK_BANK: "Which bank?",
            ResponseIntent.CANCELLED: "Transaction cancelled.",
            ResponseIntent.TRANSFER_FAILED: f"Transfer failed: {context.error_message or 'Unknown error'}",
        }
        
        return fallbacks.get(
            context.intent,
            "I'm sorry, something went wrong. Please try again."
        )


_synthesizer: Optional[ResponseSynthesizer] = None


def get_synthesizer(llm: Optional[Runnable] = None) -> ResponseSynthesizer:
    """Get or create singleton ResponseSynthesizer instance."""
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ResponseSynthesizer(llm=llm)
    elif llm and not _synthesizer.llm:
        _synthesizer.llm = llm
    return _synthesizer
