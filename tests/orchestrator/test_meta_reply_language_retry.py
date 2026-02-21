"""Tests for meta reply language mismatch retry behavior."""

from typing import Any

import pytest

from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply


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
async def test_meta_reply_retries_on_language_mismatch() -> None:
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
    assert message == "How far"


@pytest.mark.asyncio
async def test_meta_reply_falls_back_when_retry_still_wrong_language() -> None:
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
