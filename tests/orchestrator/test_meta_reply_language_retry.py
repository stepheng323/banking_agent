"""Tests for meta reply language mismatch fallback behavior."""

from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.chat.src.agent.orchestrator.models.domain import MetaIntent
from apps.chat.src.agent.orchestrator.system_profile import SystemProfile


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
async def test_brand_origin_reply_falls_back_when_llm_invents_lotr_claim() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": "Narya AI is from LOTR and named after a character from Tolkien lore.",
            }
        ]
    )
    profile = SystemProfile(
        name="Narya AI",
        description="A calm banking concierge.",
        positioning="Banking only",
        creator="Fusepay",
        brand_origin="Narya AI is named after a kindler archetype and built by Fusepay for calm, reliable banking execution.",
        supported_domains=["Send money"],
        unsupported_capabilities=["Investments"],
        tone="warm",
    )

    message, handoff = await generate_meta_reply(
        llm,
        user_message="is it from lotr",
        user_language_hint="en",
        meta_intent=MetaIntent.BRAND_ORIGIN,
        profile=profile,
    )

    assert handoff == "meta"
    assert message == profile.brand_origin


@pytest.mark.asyncio
async def test_identity_no_llm_uses_grounded_identity_message() -> None:
    profile = SystemProfile(
        name="Narya AI",
        description="A calm banking concierge.",
        positioning="Banking only",
        creator="Fusepay",
        brand_origin=None,
        supported_domains=["Send money"],
        unsupported_capabilities=["Investments"],
        tone="warm",
    )

    message, handoff = await generate_meta_reply(
        None,
        user_message="who are you",
        user_language_hint="en",
        meta_intent=MetaIntent.IDENTITY,
        profile=profile,
    )

    assert handoff == "meta"
    assert message == "I'm Narya AI. A calm banking concierge. Built by Fusepay."


@pytest.mark.asyncio
async def test_creator_reply_falls_back_to_policy_creator_when_llm_is_incorrect() -> None:
    llm = _FakeMetaLLM(
        [
            {
                "handoff": "meta",
                "language": "en",
                "message": "Narya AI was built by Unknown Labs.",
            }
        ]
    )
    profile = SystemProfile(
        name="Narya AI",
        description="A calm banking concierge.",
        positioning="Banking only",
        creator="Fusepay",
        brand_origin="Narya AI is named after a kindler archetype and built by Fusepay.",
        supported_domains=["Send money", "Buy airtime"],
        unsupported_capabilities=["Investments"],
        tone="warm",
    )

    message, handoff = await generate_meta_reply(
        llm,
        user_message="who created you",
        user_language_hint="en",
        meta_intent=MetaIntent.CREATOR,
        profile=profile,
    )

    assert handoff == "meta"
    assert message == "Narya AI was built by Fusepay."


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
    profile = SystemProfile(
        name="Narya AI",
        description="A calm banking concierge.",
        positioning="Banking only",
        creator="Fusepay",
        brand_origin="Narya AI is named after a kindler archetype and built by Fusepay.",
        supported_domains=["Send money", "Buy airtime"],
        unsupported_capabilities=["Investments"],
        tone="warm",
    )

    message, handoff = await generate_meta_reply(
        llm,
        user_message="what can you do",
        user_language_hint="en",
        meta_intent=MetaIntent.CAPABILITIES,
        profile=profile,
    )

    assert handoff == "meta"
    assert "Send money" in message
    assert "Investments" not in message


@pytest.mark.asyncio
async def test_limits_no_llm_uses_grounded_policy_limit_message() -> None:
    profile = SystemProfile(
        name="Narya AI",
        description="A calm banking concierge.",
        positioning="Banking only",
        creator="Fusepay",
        brand_origin="Narya AI is named after a kindler archetype and built by Fusepay.",
        supported_domains=["Send money", "Buy airtime"],
        unsupported_capabilities=["Investments", "International transfers"],
        tone="warm",
    )

    message, handoff = await generate_meta_reply(
        None,
        user_message="what can you not do",
        user_language_hint="en",
        meta_intent=MetaIntent.LIMITS,
        profile=profile,
    )

    assert handoff == "meta"
    assert "Investments, International transfers" in message
    assert "Send money, Buy airtime" in message
