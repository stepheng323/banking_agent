import asyncio
from typing import Any, cast

import pytest

from apps.core.src.agent.orchestrator.models.intents import RequestConfirmation, Say
from apps.core.src.messaging.presenters import telegram as telegram_presenter_module
from apps.core.src.messaging.presenters.base import PresentationContext
from apps.core.src.messaging.presenters.telegram import TelegramPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient


class _StubStreamingTelegramClient:
    def __init__(self) -> None:
        self.stream_calls: list[dict[str, Any]] = []
        self.send_text_calls: list[dict[str, Any]] = []
        self.typing_calls: list[str] = []

    async def send_text_streamed(self, **kwargs: Any) -> MessageResult:
        self.stream_calls.append(kwargs)
        await asyncio.sleep(0.02)
        return MessageResult(success=True, message_id="stream-msg-1")

    async def send_text(self, **kwargs: Any) -> MessageResult:
        self.send_text_calls.append(kwargs)
        return MessageResult(success=True, message_id="plain-msg-1")

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True


class _StubFlowTelegramClient:
    def __init__(self) -> None:
        self.flow_calls: list[dict[str, Any]] = []
        self.typing_calls: list[str] = []

    async def send_flow(self, **kwargs: Any) -> MessageResult:
        self.flow_calls.append(kwargs)
        return MessageResult(success=True, message_id="flow-msg-1")

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True


class _StubDelayedTelegramClient:
    def __init__(self, *, send_delay_seconds: float = 0.0) -> None:
        self.send_delay_seconds = send_delay_seconds
        self.typing_calls: list[str] = []
        self.send_text_calls: list[dict[str, Any]] = []

    async def send_text(self, **kwargs: Any) -> MessageResult:
        self.send_text_calls.append(kwargs)
        if self.send_delay_seconds > 0:
            await asyncio.sleep(self.send_delay_seconds)
        return MessageResult(success=True, message_id="plain-msg-1")

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True


class _StubUnitOfWork:
    users = None

    async def __aenter__(self) -> "_StubUnitOfWork":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        del exc_type, exc, tb
        return False


@pytest.mark.asyncio
async def test_telegram_presenter_say_uses_streamed_send_when_enabled() -> None:
    client = _StubStreamingTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = Say(text="Hello there this is a long enough message to stream on telegram.")
    context = PresentationContext(
        channel="telegram",
        phone_number="123456789",
        metadata={"telegram_stream_response": True, "telegram_stream_min_chars": 20},
    )

    message_id = await presenter._present_say(intent, context)

    assert message_id == "stream-msg-1"
    assert len(client.stream_calls) == 1
    assert client.send_text_calls == []


@pytest.mark.asyncio
async def test_telegram_presenter_confirmation_formats_double_asterisk_bold() -> None:
    client = _StubFlowTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = RequestConfirmation(
        task_ids=["t1"],
        summary="**Status:** Pending\n*Amount:* ₦10,000",
        token="tok-1",
        correlation_id="corr-1",
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "flow-msg-1"
    assert len(client.flow_calls) == 1
    assert client.flow_calls[0]["flow_config"]["text_body"] == "<b>Status:</b> Pending\n<b>Amount:</b> ₦10,000"


@pytest.mark.asyncio
async def test_telegram_presenter_fast_send_emits_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="short response")],
        PresentationContext(channel="telegram", phone_number="123456789", metadata={"telegram_stream_response": False}),
    )

    assert client.send_text_calls
    assert client.typing_calls == ["123456789"]


@pytest.mark.asyncio
async def test_telegram_presenter_slow_non_stream_send_emits_typing_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient(send_delay_seconds=0.02)
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="short response")],
        PresentationContext(channel="telegram", phone_number="123456789", metadata={"telegram_stream_response": False}),
    )

    assert client.send_text_calls
    assert client.typing_calls == ["123456789"]


@pytest.mark.asyncio
async def test_telegram_presenter_streamed_first_send_emits_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubStreamingTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="Hello there this is a long enough message to stream on telegram.")],
        PresentationContext(
            channel="telegram",
            phone_number="123456789",
            metadata={"telegram_stream_response": True, "telegram_stream_min_chars": 20},
        ),
    )

    assert len(client.stream_calls) == 1
    assert client.typing_calls == ["123456789"]


@pytest.mark.asyncio
async def test_telegram_presenter_suppresses_typing_when_metadata_requests_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient(send_delay_seconds=0.02)
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="short response")],
        PresentationContext(
            channel="telegram",
            phone_number="123456789",
            metadata={"telegram_stream_response": False, "suppress_typing_indicator": True},
        ),
    )

    assert client.send_text_calls
    assert client.typing_calls == []


@pytest.mark.asyncio
async def test_telegram_presenter_force_typing_indicator_sends_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient(send_delay_seconds=0.02)
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="progress update")],
        PresentationContext(
            channel="telegram",
            phone_number="123456789",
            metadata={"telegram_stream_response": False, "force_typing_indicator": True},
        ),
    )

    assert client.send_text_calls
    assert client.typing_calls == ["123456789"]


@pytest.mark.asyncio
async def test_telegram_presenter_emits_typing_before_each_outbound_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="first"), Say(text="second")],
        PresentationContext(channel="telegram", phone_number="123456789", metadata={"telegram_stream_response": False}),
    )

    assert len(client.send_text_calls) == 2
    assert client.typing_calls == ["123456789", "123456789"]
