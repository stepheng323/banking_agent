from typing import Any, cast

import pytest

from apps.core.src.agent.orchestrator.models.intents import Say
from apps.core.src.messaging.presenters.base import PresentationContext
from apps.core.src.messaging.presenters.telegram import TelegramPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient


class _StubStreamingTelegramClient:
    def __init__(self) -> None:
        self.stream_calls: list[dict[str, Any]] = []
        self.send_text_calls: list[dict[str, Any]] = []

    async def send_text_streamed(self, **kwargs: Any) -> MessageResult:
        self.stream_calls.append(kwargs)
        return MessageResult(success=True, message_id="stream-msg-1")

    async def send_text(self, **kwargs: Any) -> MessageResult:
        self.send_text_calls.append(kwargs)
        return MessageResult(success=True, message_id="plain-msg-1")


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
