from typing import Any, cast

import pytest

from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.messaging.intents import Say, ShowOptions
from shared.messaging.presenters.base import PresentationContext
from shared.messaging.presenters.telegram import TelegramPresenter
from shared.messaging.presenters.whatsapp import WhatsAppPresenter


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
async def test_whatsapp_presenter_formats_markdown_and_redirect_spacing() -> None:
    client = _StubWhatsAppClient(interactive_success=True)
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
    context = PresentationContext(channel="whatsapp", phone_number="2348000000000")
    text = (
        "Here are your account balances:\n\n"
        "• Zenith Bank (···9384): **₦30,000.00**\n"
        "No, your worth is not defined by your balance.\n"
        f"{render_message('conversational.out_of_scope', 'en')}"
    )

    message_id = await presenter._present_say(Say(text=text), context)

    assert message_id == "wa-text-1"
    assert len(client.text_calls) == 1
    sent = client.text_calls[0]["text"]
    assert "**" not in sent
    assert "*₦30,000.00*" in sent
    assert "balance.\n\nI stay on banking." in sent


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
    assert client.interactive_calls[0]["body_text"] == "I found multiple matches for Tolu. Which one?"
    assert client.interactive_calls[0]["options"] == [
        {"id": "bene:111", "title": "1. Tolu A • Access Bank • ****1234"},
        {"id": "bene:222", "title": "2. Tolu B • GTBank • ****5678"},
    ]
    assert "1. Tolu A • Access Bank • ****1234" in client.text_calls[0]["text"]
    assert "2. Tolu B • GTBank • ****5678" in client.text_calls[0]["text"]


@pytest.mark.asyncio
async def test_telegram_presenter_options_uses_explicit_button_titles() -> None:
    client = _StubTelegramClient(interactive_success=True)
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = ShowOptions(
        title="Would you like a receipt image for this transfer?",
        options=[
            {"id": "rcpt:send", "title": "Send receipt image", "button_title": "Send receipt"},
        ],
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_options(intent, context)

    assert message_id == "tg-interactive-1"
    assert client.interactive_calls[0]["body_text"] == "Would you like a receipt image for this transfer?"
    assert client.interactive_calls[0]["options"] == [
        {"id": "rcpt:send", "title": "Send receipt"},
    ]


@pytest.mark.asyncio
async def test_telegram_presenter_options_uses_compact_beneficiary_button_titles() -> None:
    client = _StubTelegramClient(interactive_success=True)
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = ShowOptions(
        title="I found multiple matches for Tolu. Which one?",
        options=[
            {
                "id": "bene:111",
                "title": "Tolu Adebayo • Access Bank • ****1234",
                "button_title": "1. Tolu Adebayo • Access • ****1234",
            },
            {
                "id": "bene:222",
                "title": "Tolu Adekunle • GTBank • ****5678",
                "button_title": "2. Tolu Adekunle • GTBank • ****5678",
            },
        ],
        task_ids=["t1"],
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_options(intent, context)

    assert message_id == "tg-interactive-1"
    assert client.interactive_calls[0]["options"] == [
        {"id": "bene:111", "title": "1. Tolu Adebayo • Access • ****1234"},
        {"id": "bene:222", "title": "2. Tolu Adekunle • GTBank • ****5678"},
    ]
