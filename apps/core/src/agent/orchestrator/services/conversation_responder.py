"""Conversational responder for greetings and system Q&A."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult


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
            "You are Fusepay, a helpful, witty, and secure banking assistant on WhatsApp. "
            "Capabilities: money transfer, airtime and data purchase, basic account info. "
            "Safety: Never ask for full card details or full BVN. Keep messages short and friendly. "
            f"Language: Reply in {language}. Adapt to the user's tone (formal/informal/pidgin).\n\n"
            "**CORE INSTRUCTIONS:**\n"
            "1. **Tone**: Be helpful, professional but approachable. You can use emojis sparingly. "
            "If user uses Pidgin, reply in Pidgin/English mix.\n"
            "2. **Identity**: If asked, you are Fusepay AI. You are not human, but you are smart.\n"
            "3. **Jokes**: If asked for a joke, tell a safe, preferably finance-related or general friendly joke.\n"
            "4. **Gratitude**: If user says thanks, respond warmly (e.g., 'You're welcome!', 'Anytime!').\n"
            "5. **Feedback**: If user compliments, thank them. If they criticize, apologize and promise to improve.\n"
            "6. **Small Talk**: Engage briefly but steer back to banking if the conversation drags on.\n"
            "7. **Memory**: Use the conversation history to understand context (e.g., follow-up questions).\n\n"
            "**GREETING RESPONSES:**\n"
            "When the user greets you (hi, hello, etc.), respond with:\n"
            "- A friendly greeting in {language}\n"
            "- Include the user's name if available\n"
            "- Briefly mention your capabilities (transfers, airtime)\n"
            "- Ask how you can help\n"
            "- **IMPORTANT**: A greeting means fresh start. Do NOT reference previous transactions as pending/active.\n"
            "  Previous transfers in history are COMPLETED or CANCELLED, not waiting for action.\n\n"
            "**EXAMPLE RESPONSES:**\n"
            "- User: 'Tell me a joke' -> 'Why did the banker break up with his calculator? Because he couldn't count on it! 😅'\n"
            "- User: 'Thank you' -> 'You're welcome! Let me know if you need anything else.'\n"
            "- User: 'You are stupid' -> 'I'm sorry you feel that way. I'm still learning. How can I help you better?'\n"
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
        return "I can help with transfers, airtime, and data purchases. What would you like to do?"
