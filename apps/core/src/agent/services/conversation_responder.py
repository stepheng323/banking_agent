"""Conversational responder for greetings and system Q&A."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.models.classification import ClassificationResult


class ConversationResponder:
    """Generates safe, friendly conversational replies."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm

    async def generate_reply(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
        user_ctx: dict[str, Any],
    ) -> str:
        lowered = text.strip().lower()
        if lowered in {"hi", "hello", "hey", "yo", "good morning", "good afternoon", "good evening"}:
            name = None
            profile = user_ctx.get("profile") or {}
            if isinstance(profile, dict):
                name = profile.get("full_name") or profile.get("first_name")
            greeting_name = f", {name}" if name else ""
            return (
                f"Hi{greeting_name}! I'm Fusepay, your AI banking assistant. "
                "I can help with transfers, airtime and data purchases. "
                "What would you like to do today?"
            )

        system = (
            "You are Fusepay, a helpful, concise banking assistant on WhatsApp. "
            "Capabilities: money transfer, airtime and data purchase, basic account info. "
            "Safety: Never ask for full card details or full BVN. Keep messages short and friendly. "
            "If the user asks about the system, briefly explain capabilities and how to proceed."
        )
        user = text.strip()
        reply = await self.llm.ainvoke(
            [{"role": "system", "content": system},
                {"role": "user", "content": user}]
        )
        if isinstance(reply, str):
            return reply
        content = getattr(reply, "content", None)
        if isinstance(content, str) and content:
            return content
        return "I can help with transfers, airtime, and data purchases. What would you like to do?"
