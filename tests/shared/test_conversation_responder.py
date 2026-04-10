import pytest

from shared.i18n import render_message
from shared.services.conversation_responder import ConversationResponder


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[dict[str, str]] | None = None

    async def ainvoke(self, _messages: list[dict[str, str]]) -> str:
        self.messages = _messages
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


@pytest.mark.asyncio
async def test_conversation_responder_uses_followup_redirect_for_second_casual_turn() -> None:
    llm = _FakeLLM("Why do banks make great musicians? They know how to handle notes.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "Another one",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "Tell me a joke"},
                {
                    "role": "assistant",
                    "content": "Why did the banker bring a ladder? To reach the next interest level.\n"
                    + render_message("conversational.out_of_scope", "en"),
                },
            ],
            "profile": {},
        },
    )

    assert reply == (
        "Why do banks make great musicians? They know how to handle notes.\n"
        + render_message("conversational.out_of_scope_followup", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_uses_firm_redirect_after_longer_casual_streak() -> None:
    llm = _FakeLLM("Quick one: the debit card said it was feeling withdrawn.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "2348000000001",
        "Another one",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "Tell me a joke"},
                {
                    "role": "assistant",
                    "content": "Bank joke.\n" + render_message("conversational.out_of_scope", "en"),
                },
                {"role": "user", "content": "Another one"},
                {
                    "role": "assistant",
                    "content": "Another bank joke.\n"
                    + render_message("conversational.out_of_scope_followup", "en"),
                },
            ],
            "profile": {},
        },
    )

    assert reply == (
        "Quick one: the debit card said it was feeling withdrawn.\n"
        + render_message("conversational.out_of_scope_firm", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_prompt_prefers_banking_related_humor_for_jokes() -> None:
    llm = _FakeLLM("Why did the bank teller smile? The balance finally checked out.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    await responder.generate_reply(
        "2348000000001",
        "Tell me a joke",
        {"language": "en", "history": [], "profile": {}},
    )

    assert llm.messages is not None
    system_prompt = llm.messages[0]["content"]
    user_prompt = llm.messages[1]["content"]
    assert "prefer banking-, money-, balance-, savings-, or transfer-themed humor" in system_prompt
    assert "Use a banking-related joke or money-themed playful line" in user_prompt
