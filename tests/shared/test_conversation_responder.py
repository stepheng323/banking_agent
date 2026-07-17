import pytest

from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    ConversationResponseMode,
    map_response_key_to_mode,
)
from banking.policy.models import CapabilityPolicy, CapabilityRule, ConversationalSuggestion, DomainCapabilityPolicy
from banking.policy.service import resolve_available_conversational_suggestions
from banking.presentation.i18n.renderer import render_message
from shared.observability.llm_call_metrics import start_llm_call_recording, stop_llm_call_recording


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[dict[str, str]] | None = None
        self.call_count = 0

    async def ainvoke(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        self.call_count += 1
        return self.reply


class _FailingLLM:
    async def ainvoke(self, _messages: list[dict[str, str]]) -> str:
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_casual_mode_returns_one_complete_generated_response() -> None:
    responder = ConversationResponder(_FakeLLM("Octopuses have three hearts. Back to banking—what can I help with?"))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Tell me something weird",
        {"language": "en", "history": [], "profile": {}},
        mode=ConversationResponseMode.CASUAL,
    )

    assert reply == "Octopuses have three hearts. Back to banking—what can I help with?"


@pytest.mark.asyncio
async def test_responder_records_mode_and_suggestion_count() -> None:
    responder = ConversationResponder(_FakeLLM("Small money joke."))  # type: ignore[arg-type]
    token = start_llm_call_recording()
    try:
        await responder.generate_reply(
            "Tell me something funny about money",
            {"language": "en", "history": [], "profile": {}},
            mode=ConversationResponseMode.CASUAL,
        )
    finally:
        calls = stop_llm_call_recording(token)

    assert len(calls) == 1
    assert calls[0]["event_name"] == "conversation_responder_llm_call"
    assert calls[0]["mode"] == "casual"
    assert calls[0]["allowed_suggestion_count"] == 6


@pytest.mark.asyncio
async def test_fresh_social_meta_prompt_omits_history_and_keeps_name() -> None:
    llm = _FakeLLM("Hey Olamide, I’m here. What banking task should we handle?")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "How far",
        {
            "language": "en",
            "history": [
                {"role": "user", "content": "my PIN is 1234"},
                {"role": "user", "content": "my account number is 1234567890"},
            ],
            "profile": {"first_name": "Olamide"},
            SOCIAL_META_RESPONSE_KEY_CTX: "conversational.greeting",
            SOCIAL_META_RENDER_PARAMS_CTX: {"display_name": "Olamide"},
        },
        mode=ConversationResponseMode.SOCIAL_META,
    )

    assert reply.startswith("Hey Olamide")
    assert llm.messages is not None
    user_prompt = llm.messages[1]["content"]
    assert "User name: Olamide" in user_prompt
    assert "my PIN is 1234" not in user_prompt
    assert "1234567890" not in user_prompt
    assert "...7890" not in user_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_reply",
    [
        "Here is investment advice: buy this stock immediately.",
        "I'll send that transfer now.",
    ],
)
async def test_social_meta_unsafe_output_uses_localized_fallback(unsafe_reply: str) -> None:
    responder = ConversationResponder(_FakeLLM(unsafe_reply))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Hi",
        {"language": "en", "history": [], SOCIAL_META_RESPONSE_KEY_CTX: "conversational.greeting"},
        mode=ConversationResponseMode.SOCIAL_META,
    )

    assert reply == render_message("conversational.greeting", "en")


@pytest.mark.asyncio
async def test_fresh_social_opener_omits_stale_unsupported_grounding() -> None:
    llm = _FakeLLM("Hi Olamide—how can I help?")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Hi",
        {
            "language": "en",
            "profile": {"first_name": "Olamide"},
            "history": [
                {"role": "user", "content": "Can you help me invest in crypto?"},
                {"role": "assistant", "content": "I can't help with crypto investing."},
            ],
            SOCIAL_META_RESPONSE_KEY_CTX: "conversational.greeting_named",
            SOCIAL_META_RENDER_PARAMS_CTX: {"display_name": "Olamide"},
        },
        mode=ConversationResponseMode.SOCIAL_META,
    )

    assert reply == "Hi Olamide—how can I help?"
    assert llm.messages is not None
    assert "crypto" not in llm.messages[1]["content"].lower()
    assert "prior refusal" in llm.messages[0]["content"]


@pytest.mark.asyncio
async def test_social_meta_stale_refusal_uses_greeting_fallback() -> None:
    responder = ConversationResponder(_FakeLLM("I can't help with crypto, but I can check your balance."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Hi",
        {
            "language": "en",
            "history": [],
            SOCIAL_META_RESPONSE_KEY_CTX: "conversational.greeting",
        },
        mode=ConversationResponseMode.SOCIAL_META,
    )

    assert reply == render_message("conversational.greeting", "en")


@pytest.mark.asyncio
async def test_clarify_mode_includes_policy_approved_suggestions_in_prompt() -> None:
    llm = _FakeLLM("Do you want to make a transfer or check your balance?")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "I handle transfers and balances",
        {"language": "en", "history": []},
        mode=ConversationResponseMode.CLARIFY,
    )

    assert reply == "Do you want to make a transfer or check your balance?"
    assert llm.messages is not None
    assert "ambiguous, unclear" in llm.messages[0]["content"]
    assert "make a transfer" in llm.messages[0]["content"]
    assert "Ask exactly one natural, focused question" in llm.messages[0]["content"]
    assert "Do not invent a missing amount, recipient, account, or prior transaction" in llm.messages[0]["content"]


@pytest.mark.asyncio
async def test_capabilities_empty_generation_uses_live_policy_fallback() -> None:
    responder = ConversationResponder(_FakeLLM(""))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "What can you do?",
        {"language": "en", "history": []},
        mode=ConversationResponseMode.CAPABILITIES,
    )

    assert "make a transfer" in reply
    assert "check your balance" in reply
    assert "What would you like to do?" in reply


@pytest.mark.asyncio
async def test_provider_failure_returns_mode_fallback() -> None:
    responder = ConversationResponder(_FailingLLM())  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "What do you mean?",
        {"language": "en", "history": []},
        mode=ConversationResponseMode.CLARIFY,
    )

    assert reply.startswith("Could you clarify")


@pytest.mark.asyncio
async def test_contextual_worker_uses_grounded_deterministic_correction() -> None:
    llm = _FakeLLM("This should not be called.")
    responder = ConversationResponder(llm)  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Nice, I thought it failed",
        {
            "language": "en",
            "history": [
                {
                    "role": "assistant",
                    "content": "This transfer of ₦10,000 to Tolu was successful.",
                }
            ],
            "contextual_worker_followup": "recent_domain_focus=support",
        },
        mode=ConversationResponseMode.CONTEXTUAL_WORKER,
    )

    assert reply == "No worries, that transfer was successful."
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_contextual_worker_rejects_action_promise() -> None:
    responder = ConversationResponder(_FakeLLM("I'll retry it now."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Okay great",
        {
            "language": "en",
            "history": [],
            "contextual_worker_followup": "recent_domain_focus=support",
        },
        mode=ConversationResponseMode.CONTEXTUAL_WORKER,
    )

    assert reply == render_message("conversational.contextual_worker_followup.generic", "en")


@pytest.mark.asyncio
async def test_unsupported_mode_rejects_capability_promise() -> None:
    responder = ConversationResponder(_FakeLLM("I can arrange that loan for you."))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Just a small loan",
        {
            "language": "en",
            "history": [],
            "unsupported_capability": {
                "key": "lending",
                "label": "loans or lending",
                "followup_count": 1,
            },
        },
        mode=ConversationResponseMode.UNSUPPORTED_BOUNDARY,
    )

    assert "cannot help with loans or lending" in reply


@pytest.mark.asyncio
async def test_contextual_meta_empty_output_uses_grounded_fallback() -> None:
    responder = ConversationResponder(_FakeLLM(""))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Okay, that's interesting",
        {
            "language": "en",
            "conversation_grounding": {
                "last_topic": "brand_origin",
                "last_assistant_message": "The name Nenya comes from the Ring of Water.",
            },
        },
        mode=ConversationResponseMode.CONTEXTUAL_META,
    )

    assert reply == render_message("conversational.contextual_meta_followup.brand_origin", "en")


@pytest.mark.asyncio
async def test_character_and_line_limits_fail_closed() -> None:
    responder = ConversationResponder(_FakeLLM("line one\nline two\nline three\nline four"))  # type: ignore[arg-type]

    reply = await responder.generate_reply(
        "Hello",
        {"language": "en", "history": [], SOCIAL_META_RESPONSE_KEY_CTX: "conversational.greeting"},
        mode=ConversationResponseMode.SOCIAL_META,
    )

    assert reply == render_message("conversational.greeting", "en")


def test_response_key_mapping_is_explicit() -> None:
    assert map_response_key_to_mode("conversational.capability_question") == ConversationResponseMode.CAPABILITIES
    assert map_response_key_to_mode("conversational.clarify") == ConversationResponseMode.CLARIFY
    assert map_response_key_to_mode("transfer.confirmation") is None


def test_policy_suggestions_are_localized_and_filter_disabled_actions() -> None:
    policy = CapabilityPolicy(
        capability_matrix={
            "transfer": DomainCapabilityPolicy(
                domain="transfer",
                actions={
                    "send_money": CapabilityRule(supported=True),
                    "disabled": CapabilityRule(supported=False),
                },
            )
        },
        conversational_suggestions=[
            ConversationalSuggestion(
                id="send_money",
                domain="transfer",
                action="send_money",
                label_key="suggestions.send_money",
            ),
            ConversationalSuggestion(
                id="disabled",
                domain="transfer",
                action="disabled",
                label_key="suggestions.buy_data",
            ),
        ],
    )

    suggestions = resolve_available_conversational_suggestions(locale="pcm", policy=policy)

    assert [(suggestion.id, suggestion.label) for suggestion in suggestions] == [("send_money", "send money")]
