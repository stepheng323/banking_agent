"""Tests for meta reply language mismatch fallback behavior."""

from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.chat.src.agent.orchestrator.models.domain import MetaIntent
from shared.assistant_profile.voice import AssistantVoice
from shared.config.settings import settings


class _FakeMetaLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._idx = 0

    def with_structured_output(self, _schema: Any) -> "_FakeMetaLLM":
        return self

    def with_config(self, _config: dict[str, Any]) -> "_FakeMetaLLM":
        return self

    async def ainvoke(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        current = self._responses[self._idx]
        self._idx += 1
        return current


def _voice(
    *,
    supported_domains: tuple[str, ...] = ("Send money",),
    unsupported_capabilities: tuple[str, ...] = ("Investments",),
    creator: str | None = settings.app_creator,
    brand_origin: str | None = f"{settings.app_name} is inspired by {settings.app_brand_inspiration}.",
) -> AssistantVoice:
    return AssistantVoice(
        name=settings.app_name,
        description="A calm banking concierge.",
        positioning="Banking only",
        creator=creator,
        brand_origin=brand_origin,
        supported_domains=supported_domains,
        unsupported_capabilities=unsupported_capabilities,
        tone_style="warm",
        brevity="short",
        response_rules=("Keep replies grounded",),
        safety_rules=("Banking tasks only",),
    )


@pytest.mark.asyncio
async def test_meta_reply_bypasses_retry_on_language_mismatch() -> None:
    llm = _FakeMetaLLM(
        [
            {"handoff": "meta", "language": "en", "message": "Hello"},
            {"handoff": "meta", "language": "pcm", "message": "How far"},
        ]
    )

    message, handoff = await generate_meta_reply(
        llm,
        user_message="how far",
        user_language_hint="pcm",
    )

    assert handoff == "meta"
    assert message.startswith("I be ")
    assert llm._idx == 1


@pytest.mark.asyncio
async def test_meta_reply_falls_back_when_language_mismatch() -> None:
    llm = _FakeMetaLLM(
        [
            {"handoff": "meta", "language": "en", "message": "Hello"},
            {"handoff": "meta", "language": "en", "message": "Hello again"},
        ]
    )

    message, handoff = await generate_meta_reply(
        llm,
        user_message="how far",
        user_language_hint="pcm",
    )

    assert handoff == "meta"
    assert message.startswith("I be ")
    assert llm._idx == 1


@pytest.mark.asyncio
async def test_brand_origin_reply_allows_grounded_lotr_origin() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": f"{settings.app_name} is inspired by {settings.app_brand_inspiration}.",
            }
        ]
    )
    voice = _voice()

    message, handoff = await generate_meta_reply(
        llm,
        user_message="is it from lotr",
        user_language_hint="en",
        meta_intent=MetaIntent.BRAND_ORIGIN,
        voice=voice,
    )

    assert handoff == "meta"
    assert message == f"{settings.app_name} is inspired by {settings.app_brand_inspiration}."


@pytest.mark.asyncio
async def test_brand_origin_reply_falls_back_when_llm_over_specifies_lotr_claim() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": f"{settings.app_name} is the ring of power from Tolkien lore.",
            }
        ]
    )
    voice = _voice()

    message, handoff = await generate_meta_reply(
        llm,
        user_message="is it from lotr",
        user_language_hint="en",
        meta_intent=MetaIntent.BRAND_ORIGIN,
        voice=voice,
    )

    assert handoff == "meta"
    assert message == voice.brand_origin


@pytest.mark.asyncio
async def test_identity_no_llm_uses_grounded_identity_message() -> None:
    voice = _voice(brand_origin=None)

    message, handoff = await generate_meta_reply(
        None,
        user_message="who are you",
        user_language_hint="en",
        meta_intent=MetaIntent.IDENTITY,
        voice=voice,
    )

    assert handoff == "meta"
    assert message == f"I'm {settings.app_name}. A calm banking concierge. Built by {settings.app_creator}."


@pytest.mark.asyncio
async def test_creator_reply_falls_back_to_policy_creator_when_llm_is_incorrect() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": f"{settings.app_name} was built by Unknown Labs.",
            }
        ]
    )
    voice = _voice(supported_domains=("Send money", "Buy airtime"))

    message, handoff = await generate_meta_reply(
        llm,
        user_message="who created you",
        user_language_hint="en",
        meta_intent=MetaIntent.CREATOR,
        voice=voice,
    )

    assert handoff == "meta"
    assert message == f"{settings.app_name} was built by {settings.app_creator}."


@pytest.mark.asyncio
async def test_capabilities_reply_blocks_unsupported_claims() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": "I can help with transfers and investments.",
            }
        ]
    )
    voice = _voice(supported_domains=("Send money", "Buy airtime"))

    message, handoff = await generate_meta_reply(
        llm,
        user_message="what can you do",
        user_language_hint="en",
        meta_intent=MetaIntent.CAPABILITIES,
        voice=voice,
    )

    assert handoff == "meta"
    assert "Send money" in message
    assert "Investments" not in message


@pytest.mark.asyncio
async def test_limits_no_llm_uses_grounded_policy_limit_message() -> None:
    voice = _voice(
        supported_domains=("Send money", "Buy airtime"),
        unsupported_capabilities=("Investments", "International transfers"),
    )

    message, handoff = await generate_meta_reply(
        None,
        user_message="what can you not do",
        user_language_hint="en",
        meta_intent=MetaIntent.LIMITS,
        voice=voice,
    )

    assert handoff == "meta"
    assert "Investments, International transfers" in message
    assert "Send money, Buy airtime" in message
