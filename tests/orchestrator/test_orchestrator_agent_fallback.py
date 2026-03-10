"""Tests for OrchestratorAgent fallback behavior."""

from types import SimpleNamespace
from typing import Any

import pytest

from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from shared.i18n import LocaleCode, LocaleManager, render_message


class _ContextManagerStub:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, str]] = []

    async def add_conversation_turn(self, phone_number: str, role: str, text: str) -> None:
        self.turns.append((phone_number, role, text))


class _HandlerStub:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    async def invoke(self, context: Any) -> dict[str, Any]:
        del context
        return dict(self._result)


@pytest.mark.asyncio
async def test_invoke_does_not_inject_processing_error_when_flow_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=lambda *args, **kwargs: None,
            get_image_data=lambda *args, **kwargs: None,
        )
    )
    agent.context_manager = _ContextManagerStub()
    agent.orchestrator_handler = _HandlerStub(
        {
            "text": None,
            "intents": [object()],
            "outbox": [{"type": "flow", "flow_id": "flow_1", "flow_config": {}}],
            "locale": "en",
        }
    )

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))

    scheduled: list[Any] = []

    def _fake_create_background_task(coro: Any) -> None:
        scheduled.append(coro)

    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.orchestrator.create_background_task", _fake_create_background_task)

    result = await agent.invoke(
        phone_number="2348000000010",
        text="link another account",
        message_id="wamid-0010",
    )

    for coro in scheduled:
        await coro

    assert result["text"] is None
    assert result["locale"] == "en"
    assert agent.context_manager.turns == [("2348000000010", "user", "link another account")]


@pytest.mark.asyncio
async def test_invoke_keeps_processing_error_when_no_text_or_interaction(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=lambda *args, **kwargs: None,
            get_image_data=lambda *args, **kwargs: None,
        )
    )
    agent.context_manager = _ContextManagerStub()
    agent.orchestrator_handler = _HandlerStub({"text": None, "intents": [], "outbox": [], "locale": "en"})

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))

    scheduled: list[Any] = []

    def _fake_create_background_task(coro: Any) -> None:
        scheduled.append(coro)

    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.orchestrator.create_background_task", _fake_create_background_task)

    result = await agent.invoke(
        phone_number="2348000000011",
        text="...",
        message_id="wamid-0011",
    )

    for coro in scheduled:
        await coro

    assert result["text"] == render_message("orchestrator.fallback.processing_error", "en")
    assert agent.context_manager.turns[-1] == (
        "2348000000011",
        "assistant",
        render_message("orchestrator.fallback.processing_error", "en"),
    )
