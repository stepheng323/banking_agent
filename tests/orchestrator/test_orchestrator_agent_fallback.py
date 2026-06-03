"""Tests for OrchestratorAgent fallback behavior."""

from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.services.media_service import MediaInterpretation
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LocaleCode
from banking.presentation.i18n.renderer import render_message


class _ContextManagerStub:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, str]] = []

    async def add_conversation_turn(
        self,
        phone_number: str,
        role: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        self.turns.append((phone_number, role, text))


class _HandlerStub:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.last_context: Any | None = None

    async def invoke(self, context: Any) -> dict[str, Any]:
        self.last_context = context
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

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.agent.create_background_task", _fake_create_background_task)

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

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.agent.create_background_task", _fake_create_background_task)

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


@pytest.mark.asyncio
async def test_invoke_allows_silent_async_completion_when_fallback_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        {"text": None, "intents": [], "outbox": [], "locale": "en", "suppress_empty_fallback": True}
    )

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))

    scheduled: list[Any] = []

    def _fake_create_background_task(coro: Any) -> None:
        scheduled.append(coro)

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.agent.create_background_task", _fake_create_background_task)

    result = await agent.invoke(
        phone_number="2348000000012",
        text="yes",
        message_id="wamid-0012",
    )

    for coro in scheduled:
        await coro

    assert result["text"] is None
    assert agent.context_manager.turns == [("2348000000012", "user", "yes")]


@pytest.mark.asyncio
async def test_image_caption_and_extraction_become_downstream_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _interpret_image(*args: Any, **kwargs: Any) -> MediaInterpretation:
        assert kwargs["mime_type"] == "image/png"
        return MediaInterpretation(
            source="image",
            text="Extracted from image: recipient_account=8162511023; bank_name=OPay.",
            entities={"recipient_account": "8162511023", "bank_name": "OPay"},
            confidence=0.9,
        )

    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=lambda *args, **kwargs: None,
            interpret_image=_interpret_image,
        )
    )
    agent.context_manager = _ContextManagerStub()
    handler = _HandlerStub({"text": "ok", "intents": [], "outbox": [], "locale": "en"})
    agent.orchestrator_handler = handler

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))
    scheduled: list[Any] = []
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.agent.create_background_task",
        lambda coro: scheduled.append(coro),
    )

    result = await agent.invoke(
        phone_number="2348000000012",
        text="send 5k for groceries",
        message_id="wamid-image",
        message_type="image",
        media_id="media-1",
        mime_type="image/png",
    )

    for coro in scheduled:
        await coro

    assert result["text"] == "ok"
    assert handler.last_context.text == (
        "User caption/instruction: send 5k for groceries\n"
        "Caption-derived transfer fields: amount=5000.0.\n"
        "Caption-derived transfer fields: narration=groceries.\n\n"
        "Extracted from image: recipient_account=8162511023; bank_name=OPay."
    )
    assert handler.last_context.image_data is None
    assert "data:image" not in handler.last_context.text
    assert agent.context_manager.turns[0] == (
        "2348000000012",
        "user",
        "User caption/instruction: send 5k for groceries\n"
        "Caption-derived transfer fields: amount=5000.0.\n"
        "Caption-derived transfer fields: narration=groceries.\n\n"
        "Extracted from image: recipient_account=8162511023; bank_name=OPay.",
    )


@pytest.mark.asyncio
async def test_image_only_details_are_described_as_media_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _interpret_image(*args: Any, **kwargs: Any) -> MediaInterpretation:
        del args, kwargs
        return MediaInterpretation(
            source="image",
            text="Extracted from image: recipient_account=8162511023; bank_name=OPay.",
            entities={"recipient_account": "8162511023", "bank_name": "OPay"},
            confidence=0.9,
        )

    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=lambda *args, **kwargs: None,
            interpret_image=_interpret_image,
        )
    )
    agent.context_manager = _ContextManagerStub()
    handler = _HandlerStub({"text": "ok", "intents": [], "outbox": [], "locale": "en"})
    agent.orchestrator_handler = handler

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))
    scheduled: list[Any] = []
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.agent.create_background_task",
        lambda coro: scheduled.append(coro),
    )

    await agent.invoke(
        phone_number="2348000000013",
        text="",
        message_id="wamid-image-only",
        message_type="image",
        media_id="media-1",
        mime_type="image/jpeg",
    )

    for coro in scheduled:
        await coro

    assert handler.last_context.text == (
        "User sent an image with recipient bank details.\n\n"
        "Extracted from image: recipient_account=8162511023; bank_name=OPay."
    )
    assert handler.last_context.image_data is None


@pytest.mark.asyncio
async def test_failed_image_extraction_without_caption_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _interpret_image(*args: Any, **kwargs: Any) -> MediaInterpretation:
        del args, kwargs
        return MediaInterpretation(source="image", error="unreadable")

    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=lambda *args, **kwargs: None,
            interpret_image=_interpret_image,
        )
    )
    agent.context_manager = _ContextManagerStub()
    handler = _HandlerStub({"text": "should not run", "intents": [], "outbox": [], "locale": "en"})
    agent.orchestrator_handler = handler

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))
    scheduled: list[Any] = []
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.agent.create_background_task",
        lambda coro: scheduled.append(coro),
    )

    result = await agent.invoke(
        phone_number="2348000000014",
        text="",
        message_id="wamid-image-fail",
        message_type="image",
        media_id="media-1",
    )

    for coro in scheduled:
        await coro

    prompt = render_message("orchestrator.error.image_unprocessable", "en")
    assert result == {"text": prompt, "intents": [], "outbox": [], "locale": "en"}
    assert handler.last_context is None
    assert agent.context_manager.turns == [
        ("2348000000014", "user", ""),
        ("2348000000014", "assistant", prompt),
    ]


@pytest.mark.asyncio
async def test_audio_transcription_combines_with_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _process_audio(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "account is 8162511023 OPay"

    agent = object.__new__(OrchestratorAgent)
    agent.message_type = "text"
    agent.deps = SimpleNamespace(
        media_service=SimpleNamespace(
            process_audio=_process_audio,
            interpret_image=lambda *args, **kwargs: None,
        )
    )
    agent.context_manager = _ContextManagerStub()
    handler = _HandlerStub({"text": "ok", "intents": [], "outbox": [], "locale": "en"})
    agent.orchestrator_handler = handler

    async def _effective_locale(cls, phone_number: str, detected_language: str | None = None) -> LocaleCode:
        del cls, phone_number, detected_language
        return LocaleCode.EN

    monkeypatch.setattr(LocaleManager, "get_effective_locale", classmethod(_effective_locale))
    scheduled: list[Any] = []
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.agent.create_background_task",
        lambda coro: scheduled.append(coro),
    )

    await agent.invoke(
        phone_number="2348000000015",
        text="send 5k",
        message_id="wamid-audio",
        message_type="audio",
        media_id="voice-1",
    )

    for coro in scheduled:
        await coro

    assert handler.last_context.text == "send 5k\n\nTranscribed audio: account is 8162511023 OPay"
