import pytest

from shared.i18n import render_message
from shared.services.conversation_responder import ConversationResponder


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def ainvoke(self, _messages: list[dict[str, str]]) -> str:
        return self.reply


@pytest.mark.asyncio
async def test_conversation_responder_appends_deterministic_banking_redirect() -> None:
    responder = ConversationResponder(_FakeLLM("Today is Thursday, April 09, 2026.") )  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "What's today's date?",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == (
        "Today is Thursday, April 09, 2026.\n"
        + render_message("conversational.out_of_scope", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_falls_back_to_redirect_for_unsafe_output() -> None:
    responder = ConversationResponder(_FakeLLM("Here is some investment advice: buy this stock immediately."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "What should I invest in?",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == render_message("conversational.out_of_scope", "en")


@pytest.mark.asyncio
async def test_conversation_responder_keeps_harmless_joke_reply_plus_redirect() -> None:
    responder = ConversationResponder(_FakeLLM("Why did the banker bring a ladder? To reach the next interest level."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "Tell me a joke",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == (
        "Why did the banker bring a ladder? To reach the next interest level.\n"
        + render_message("conversational.out_of_scope", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_rejects_banking_only_refusal_for_harmless_chat() -> None:
    responder = ConversationResponder(
        _FakeLLM("Sorry, I can't provide jokes - I'm here to help with your banking tasks only.")
    )  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "Tell me a joke",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == render_message("conversational.out_of_scope", "en")
