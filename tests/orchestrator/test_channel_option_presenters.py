from typing import Any, cast

import pytest

from apps.chat.src.agent.orchestrator.models.intents import ShowOptions
from apps.chat.src.messaging.presenters.base import PresentationContext
from apps.chat.src.messaging.presenters.telegram import TelegramPresenter
from apps.chat.src.messaging.presenters.whatsapp import WhatsAppPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient


class _StubWhatsAppClient:
    def __init__(self, *, interactive_success: bool) -> None:
        self.interactive_success = interactive_success
        self.interactive_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    async def send_interactive(self, **kwargs: Any) -> MessageResult:
        self.interactive_calls.append(kwargs)
        if self.interactive_success:
            return MessageResult(success=True, message_id="wa-interactive-1")
        return MessageResult(success=False, error="interactive_failed")

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"messages": [{"id": "wa-text-1"}]}


class _StubTelegramClient:
    def __init__(self, *, interactive_success: bool) -> None:
        self.interactive_success = interactive_success
        self.interactive_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    async def send_interactive(self, **kwargs: Any) -> MessageResult:
        self.interactive_calls.append(kwargs)
        if self.interactive_success:
            return MessageResult(success=True, message_id="tg-interactive-1")
        return MessageResult(success=False, error="interactive_failed")

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"message_id": "tg-text-1"}


@pytest.mark.asyncio
async def test_whatsapp_presenter_options_uses_interactive_when_available() -> None:
    client = _StubWhatsAppClient(interactive_success=True)
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
    intent = ShowOptions(
        title="Which account would you like to use?",
        options=[
            {"id": "1", "title": "Access (···1234)"},
            {"id": "2", "title": "GTBank (···5678)"},
        ],
        task_ids=["t1"],
    )
    context = PresentationContext(channel="whatsapp", phone_number="2348000000000")

    message_id = await presenter._present_options(intent, context)

    assert message_id == "wa-interactive-1"
    assert len(client.interactive_calls) == 1
    assert not client.text_calls


@pytest.mark.asyncio
async def test_telegram_presenter_options_falls_back_to_numbered_text() -> None:
    client = _StubTelegramClient(interactive_success=False)
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = ShowOptions(
        title="I found multiple matches for Tolu. Which one?",
        options=[
            {"id": "bene:111", "title": "Tolu A • Access Bank • ****1234"},
            {"id": "bene:222", "title": "Tolu B • GTBank • ****5678"},
        ],
        task_ids=["t1"],
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_options(intent, context)

    assert message_id == "tg-text-1"
    assert len(client.interactive_calls) == 1
    assert len(client.text_calls) == 1
    assert "1. Tolu A • Access Bank • ****1234" in client.interactive_calls[0]["body_text"]
    assert client.interactive_calls[0]["options"] == [
        {"id": "bene:111", "title": "1"},
        {"id": "bene:222", "title": "2"},
    ]
    assert "1. Tolu A • Access Bank • ****1234" in client.text_calls[0]["text"]
    assert "2. Tolu B • GTBank • ****5678" in client.text_calls[0]["text"]
