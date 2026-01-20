"""Conversation responder service for generating conversational replies."""

from typing import Any

from langchain_openai import ChatOpenAI


class ConversationResponder:
    """Generates conversational replies for complex cases (FAQ, support fallback)."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm

    async def generate_reply(
        self,
        phone_number: str,
        text: str,
        user_ctx: dict[str, Any],
        intent: str | None = None,
    ) -> str:
        """Generate LLM reply for complex conversational cases."""
        name = None
        profile = user_ctx.get("profile") or {}
        if isinstance(profile, dict):
            name = profile.get("full_name") or profile.get("first_name")

        language = user_ctx.get("language") or "English"
        history = user_ctx.get("history") or []

        history_text = ""
        if history:
            history_text = "\n\n**CONVERSATION HISTORY (Last 5 turns):**\n"
            for turn in history[-5:]:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                history_text += f"{role.upper()}: {content}\n"

        system = (
            "You are Fusepay, a helpful banking assistant on WhatsApp. "
            f"Reply in {language}. Adapt to the user's tone.\n\n"
            "**INSTRUCTIONS:**\n"
            "1. Be helpful, professional but approachable. Use emojis sparingly.\n"
            "2. If user uses Pidgin, reply in Pidgin/English mix.\n"
            "3. If asked, you are Fusepay AI.\n"
            "4. For jokes, tell safe, finance-related or general friendly jokes.\n"
            "5. For thanks, respond warmly.\n"
            "6. For criticism, apologize and promise to improve.\n"
        )

        user_input = text.strip()
        if name:
            user_input = f"{user_input}\n\n[User's name: {name}]"

        reply = await self.llm.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{history_text}\n\nUser message: {user_input}"},
            ]
        )

        if isinstance(reply, str):
            return reply
        content = getattr(reply, "content", None)
        if isinstance(content, str) and content:
            return content
        return "How can I help you today?"
