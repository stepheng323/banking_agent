import pytest

from shared.i18n.renderer import render_message
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import unsupported_capability_params
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import get_unsupported_capability


def _unsupported_params(key: str) -> dict[str, object]:
    capability = get_unsupported_capability(key)
    assert capability is not None
    return unsupported_capability_params(capability)


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
        "What's today's date?",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == (
        "Today is Thursday, April 09, 2026.\n"
        + render_message("conversational.out_of_scope", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_omits_redirect_for_banking_result_reaction() -> None:
    llm = _FakeLLM("No, Olamide - your worth is not defined by your balance.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "So, i am a poor man?",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "What my balance"},
                {
                    "role": "assistant",
                    "content": (
                        "Here are your account balances:\n\n"
                        "• Zenith Bank (···9384): ₦30,000.00\n\n"
                        "That gives you a total of ₦120,000.00."
                    ),
                },
            ],
            "profile": {"first_name": "Olamide"},
        },
    )

    assert reply == "No, Olamide - your worth is not defined by your balance."
    assert render_message("conversational.out_of_scope", "en") not in reply
    assert llm.messages is not None
    assert "reacting to recent banking information" in llm.messages[0]["content"]


@pytest.mark.asyncio
async def test_conversation_responder_keeps_redirect_for_reaction_without_banking_result() -> None:
    responder = ConversationResponder(_FakeLLM("No, your worth is not defined by money."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "So, i am a poor man?",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == (
        "No, your worth is not defined by money.\n"
        + render_message("conversational.out_of_scope", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_falls_back_to_redirect_for_unsafe_output() -> None:
    responder = ConversationResponder(_FakeLLM("Here is some investment advice: buy this stock immediately."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "What should I invest in?",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == render_message("conversational.out_of_scope", "en")


@pytest.mark.asyncio
async def test_conversation_responder_keeps_harmless_joke_reply_plus_redirect() -> None:
    responder = ConversationResponder(_FakeLLM("Why did the banker bring a ladder? To reach the next interest level."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
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
        "Tell me a joke",
        {"language": "en", "history": [], "profile": {}},
    )

    assert reply == (
        "Why did the banker bring a ladder? To reach the next interest level.\n"
        + render_message("conversational.out_of_scope", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_uses_followup_redirect_for_second_casual_turn() -> None:
    llm = _FakeLLM("Why do banks make great musicians? They know how to handle notes.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
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
        "Tell me a joke",
        {"language": "en", "history": [], "profile": {}},
    )

    assert llm.messages is not None
    system_prompt = llm.messages[0]["content"]
    user_prompt = llm.messages[1]["content"]
    assert "prefer banking-, money-, balance-, savings-, or transfer-themed humor" in system_prompt
    assert "Use a banking-related joke or money-themed playful line" in user_prompt


@pytest.mark.asyncio
async def test_conversation_responder_treats_one_more_as_joke_followup_from_history() -> None:
    llm = _FakeLLM("Why did the savings account relax? It had strong interest.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "One more",
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
        "Why did the savings account relax? It had strong interest.\n"
        + render_message("conversational.out_of_scope_followup", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_uses_deterministic_joke_fallback_when_llm_returns_refusal() -> None:
    responder = ConversationResponder(
        _FakeLLM("Sorry, I can't provide jokes - I'm here to help with your banking tasks only.")
    )  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Another one",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "Tell me a joke"},
                {
                    "role": "assistant",
                    "content": "Bank joke.\n" + render_message("conversational.out_of_scope", "en"),
                },
            ],
            "profile": {},
        },
    )

    assert reply == (
        "Why do bankers love balance? Because it always checks out.\n"
        + render_message("conversational.out_of_scope_followup", "en")
    )


@pytest.mark.asyncio
async def test_conversation_responder_stops_generating_after_casual_spam_threshold() -> None:
    llm = _FakeLLM("This should never be used.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Another one",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "Tell me a joke"},
                {
                    "role": "assistant",
                    "content": "Bank joke one.\n" + render_message("conversational.out_of_scope", "en"),
                },
                {"role": "user", "content": "Another one"},
                {
                    "role": "assistant",
                    "content": "Bank joke two.\n" + render_message("conversational.out_of_scope_followup", "en"),
                },
                {"role": "user", "content": "Again"},
                {
                    "role": "assistant",
                    "content": "Bank joke three.\n" + render_message("conversational.out_of_scope_firm", "en"),
                },
            ],
            "profile": {},
        },
    )

    assert reply == render_message("conversational.out_of_scope_firm", "en")
    assert llm.messages is None


@pytest.mark.asyncio
async def test_conversation_responder_contextual_worker_followup_omits_redirect() -> None:
    llm = _FakeLLM("Got it, that transfer is settled.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Ok great",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "Show the details"},
                {
                    "role": "assistant",
                    "content": "This transfer of ₦10,000 to Tolu Adebayo was successful.",
                },
            ],
            "profile": {},
            "contextual_worker_followup": "recent_domain_focus=support\nsupport_context={'last_transaction_ref': 'tx-1'}",
        },
        intent="contextual_worker_followup",
    )

    assert reply == "Got it, that transfer is settled."
    assert render_message("conversational.out_of_scope", "en") not in reply
    assert llm.messages is not None
    assert "No generic banking redirect" in llm.messages[0]["content"]
    assert "Recent banking context:" in llm.messages[1]["content"]
    assert "last_transaction_ref" in llm.messages[1]["content"]


@pytest.mark.asyncio
async def test_conversation_responder_contextual_worker_followup_grounded_failure_correction() -> None:
    llm = _FakeLLM("Glad it looked better than expected.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "nice, nice. I thought it failed",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "show the details"},
                {
                    "role": "assistant",
                    "content": "This transfer of ₦10,000 to Tolu Adebayo was successful on May 17.",
                },
            ],
            "profile": {},
            "contextual_worker_followup": "recent_domain_focus=support",
        },
        intent="contextual_worker_followup",
    )

    assert reply == "No worries, that transfer was successful."
    assert llm.messages is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locale", "text", "expected"),
    [
        ("pcm", "no wahala, i bin think say e fail", "No wahala, that transfer successful."),
        ("yo", "o dara, mo ro pe o kuna", "Ko si wahala, transfer naa ṣaṣeyọri."),
        ("ha", "na gane, na dauka ya fadi", "Ba damuwa, wannan transfer ya yi nasara."),
        ("ig", "o di mma, echere m na o fail", "Enweghị nsogbu, transfer ahụ gara nke ọma."),
    ],
)
async def test_conversation_responder_contextual_worker_followup_grounded_multilingual(
    locale: str,
    text: str,
    expected: str,
) -> None:
    llm = _FakeLLM("This should not be used.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        text,
        {
            "language": locale,
            "history": [
                {"role": "user", "content": "show the details"},
                {
                    "role": "assistant",
                    "content": "This transfer of ₦10,000 to Tolu Adebayo was successful on May 17.",
                },
            ],
            "profile": {},
            "contextual_worker_followup": "recent_domain_focus=support",
        },
        intent="contextual_worker_followup",
    )

    assert reply == expected
    assert llm.messages is None


@pytest.mark.asyncio
async def test_conversation_responder_contextual_worker_followup_rejects_action_promises() -> None:
    responder = ConversationResponder(_FakeLLM("I'll retry it now."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Okay great, I thought it failed",
        {
            "language": "en",
            "history": [],
            "profile": {},
            "contextual_worker_followup": "recent_domain_focus=support",
        },
        intent="contextual_worker_followup",
    )

    assert reply == render_message("conversational.contextual_worker_followup.settled", "en")


@pytest.mark.asyncio
async def test_conversation_responder_unsupported_capability_followup_is_bounded() -> None:
    llm = _FakeLLM("I get why you're asking, but I can't lend money or arrange loans here.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Just a small amount please",
        {
            "language": "en",
            "history": [],
            "profile": {},
            "unsupported_capability": {
                "key": "lending",
                "label": "loans or lending",
                "followup_count": 1,
                "supported_alternatives": "transfers, airtime/data, balances, and transaction queries",
            },
        },
        intent="unsupported_capability_followup",
    )

    assert reply == "I get why you're asking, but I can't lend money or arrange loans here."
    assert llm.messages is not None
    assert "unsupported capability: loans or lending" in llm.messages[0]["content"]
    assert "Do not mention or use stale transfer" in llm.messages[0]["content"]
    assert "Unsupported follow-up count: 1" in llm.messages[1]["content"]


@pytest.mark.asyncio
async def test_conversation_responder_unsupported_capability_rejects_loan_promises() -> None:
    responder = ConversationResponder(_FakeLLM("I can arrange that loan for you."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Just a small amount please",
        {
            "language": "en",
            "history": [],
            "profile": {},
            "unsupported_capability": {
                "key": "lending",
                "label": "loans or lending",
                "followup_count": 1,
            },
        },
        intent="unsupported_capability_followup",
    )

    assert reply == render_message("capability.unsupported_unavailable_followup", "en", _unsupported_params("lending"))


@pytest.mark.asyncio
async def test_conversation_responder_unsupported_capability_rejects_crypto_promises() -> None:
    responder = ConversationResponder(_FakeLLM("I can buy bitcoin for you."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Just small bitcoin please",
        {
            "language": "en",
            "history": [],
            "profile": {},
            "unsupported_capability": {
                "key": "investments",
                "label": "investments or crypto",
                "followup_count": 1,
            },
        },
        intent="unsupported_capability_followup",
    )

    assert reply == render_message(
        "capability.unsupported_unavailable_followup",
        "en",
        _unsupported_params("investments"),
    )


@pytest.mark.asyncio
async def test_conversation_responder_contextual_meta_followup_uses_grounded_fallback() -> None:
    responder = ConversationResponder(_FakeLLM(""))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Okay, that's mental",
        {
            "language": "en",
            "conversation_grounding": {
                "display_name": "Gaines",
                "last_topic": "brand_origin",
                "last_assistant_message": "The name Nenya comes from the Ring of Water.",
                "recent_turns": [
                    {"role": "user", "content": "What is the meaning of Nenya?"},
                    {"role": "assistant", "content": "The name Nenya comes from the Ring of Water."},
                ],
            },
        },
        intent="contextual_meta_followup",
    )

    assert reply == render_message("conversational.contextual_meta_followup.brand_origin", "en")


@pytest.mark.asyncio
async def test_conversation_responder_contextual_meta_prompt_includes_safe_grounding() -> None:
    llm = _FakeLLM("Yeah, that's the flow idea in plain language.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "cool",
        {
            "language": "en",
            "profile": {"first_name": "Gaines"},
            "conversation_grounding": {
                "display_name": "Gaines",
                "last_topic": "brand_origin",
                "last_assistant_message": "The name Nenya comes from the Ring of Water.",
                "recent_turns": [
                    {"role": "assistant", "content": "The name Nenya comes from the Ring of Water."}
                ],
            },
        },
        intent="contextual_meta_followup",
    )

    assert reply == "Yeah, that's the flow idea in plain language."
    assert llm.messages is not None
    assert "Conversation last topic: brand_origin" in llm.messages[1]["content"]
    assert "Last assistant message: The name Nenya comes from the Ring of Water." in llm.messages[1]["content"]
    assert "User name: Gaines" in llm.messages[1]["content"]
