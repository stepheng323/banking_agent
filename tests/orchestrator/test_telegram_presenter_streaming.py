import asyncio
from typing import Any, cast

import pytest

from apps.chat.src.agent.orchestrator.models.intents import RequestAuth, RequestConfirmation, Say
from apps.chat.src.messaging.presenters.base import PresentationContext
from apps.chat.src.messaging.presenters.telegram import TelegramPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.messaging.presenters import telegram as telegram_presenter_module


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
        self.send_text_calls: list[dict[str, Any]] = []
        self.typing_calls: list[str] = []

    async def send_flow(self, **kwargs: Any) -> MessageResult:
        self.flow_calls.append(kwargs)
        return MessageResult(success=True, message_id="flow-msg-1")

    async def send_text(self, **kwargs: Any) -> MessageResult:
        self.send_text_calls.append(kwargs)
        return MessageResult(success=True, message_id="plain-msg-1")

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True


class _StubInteractiveTelegramClient:
    def __init__(self) -> None:
        self.interactive_calls: list[dict[str, Any]] = []
        self.send_text_calls: list[dict[str, Any]] = []

    async def send_interactive(self, **kwargs: Any) -> MessageResult:
        self.interactive_calls.append(kwargs)
        return MessageResult(success=True, message_id="interactive-msg-1")

    async def send_text(self, **kwargs: Any) -> MessageResult:
        self.send_text_calls.append(kwargs)
        return MessageResult(success=True, message_id="plain-msg-1")

    async def send_typing_indicator(self, chat_id: str) -> bool:
        del chat_id
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
async def test_telegram_presenter_say_uses_inline_buttons_from_actionable_payload() -> None:
    client = _StubInteractiveTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = Say(
        text="Transactions\nShowing 1-5 of 37",
        actionable_payload={
            "telegram_inline_buttons": [
                {"id": "Previous page", "title": "Back"},
                {"id": "Next page", "title": "Next"},
            ]
        },
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_say(intent, context)

    assert message_id == "interactive-msg-1"
    assert client.interactive_calls == [
        {
            "to": "123456789",
            "body_text": "Transactions\nShowing 1-5 of 37",
            "options": [
                {"id": "Previous page", "title": "Back"},
                {"id": "Next page", "title": "Next"},
            ],
            "suppress_typing_indicator": True,
        }
    ]
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
        header="Confirm Transfer",
    )
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "flow-msg-1"
    assert len(client.flow_calls) == 1
    assert client.flow_calls[0]["flow_config"]["header"] == "Confirm Transfer"
    assert client.flow_calls[0]["flow_config"]["text_body"] == "<b>Status:</b> Pending\n<b>Amount:</b> ₦10,000"


@pytest.mark.asyncio
async def test_telegram_presenter_schedule_update_confirmation_uses_text_not_pin_flow() -> None:
    client = _StubFlowTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = RequestConfirmation(
        task_ids=["t1"],
        summary="Transfer: ₦20,000 Mum • One Time at 9:00 AM WAT",
        token="tok-1",
        correlation_id="corr-1",
        header="Confirm Schedule Update",
    )
    intent.actionable_payload = {"task_type": "schedule", "action": "edit_scheduled_transaction"}
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "plain-msg-1"
    assert client.flow_calls == []
    assert client.send_text_calls[0]["text"].startswith("Confirm Schedule Update")
    assert "Reply yes to confirm" in client.send_text_calls[0]["text"]


@pytest.mark.asyncio
async def test_telegram_presenter_schedule_auth_uses_schedule_pin_prefix() -> None:
    client = _StubFlowTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = RequestAuth(
        method="pin",
        task_ids=["t1"],
        correlation_id="corr-1",
        reason="Authorize Schedule Update",
        summary="Confirm schedule update",
    )
    intent.actionable_payload = {"task_type": "schedule", "action": "edit_scheduled_transaction"}
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_auth(intent, context)

    assert message_id == "flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "schedule-pin-corr-1-123456789"
    assert client.flow_calls[0]["flow_config"]["flow_cta"] == "Authorize Update"


@pytest.mark.asyncio
async def test_telegram_presenter_batch_confirmation_uses_prefix_for_correlation_task() -> None:
    client = _StubFlowTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = RequestConfirmation(
        task_ids=["t_transfer", "t_airtime"],
        summary="*Transfer*\nConfirm transfer task\n\n*Airtime*\nConfirm airtime task",
        token="tok-1",
        correlation_id="idem-transfer",
        header="Confirm Transactions",
    )
    intent.actionable_payload = {
        "task_type": "batch",
        "tasks": [
            {"task_type": "transfer", "idempotency_key": "idem-transfer"},
            {"task_type": "airtime", "idempotency_key": "idem-airtime"},
        ],
    }
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "transfer-pin-idem-transfer-123456789"


@pytest.mark.asyncio
async def test_telegram_presenter_batch_auth_uses_prefix_for_correlation_task() -> None:
    client = _StubFlowTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    intent = RequestAuth(
        method="pin",
        task_ids=["t_data", "t_airtime"],
        correlation_id="idem-data",
        reason="Authorize Transaction",
        summary="*Data*\nConfirm data task\n\n*Airtime*\nConfirm airtime task",
    )
    intent.actionable_payload = {
        "task_type": "batch",
        "tasks": [
            {"task_type": "data", "idempotency_key": "idem-data"},
            {"task_type": "airtime", "idempotency_key": "idem-airtime"},
        ],
    }
    context = PresentationContext(channel="telegram", phone_number="123456789")

    message_id = await presenter._present_auth(intent, context)

    assert message_id == "flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "data-pin-idem-data-123456789"


@pytest.mark.asyncio
async def test_telegram_presenter_send_typing_intent_emits_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))
    from apps.chat.src.agent.orchestrator.models.intents import SendTyping

    await presenter.present(
        [SendTyping()],
        PresentationContext(channel="telegram", phone_number="123456789"),
    )

    assert client.typing_calls == ["123456789"]


@pytest.mark.asyncio
async def test_telegram_presenter_say_no_longer_emits_implicit_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_presenter_module, "UnitOfWork", _StubUnitOfWork)
    client = _StubDelayedTelegramClient()
    presenter = TelegramPresenter(cast(MessagingClient, client))

    await presenter.present(
        [Say(text="short response")],
        PresentationContext(channel="telegram", phone_number="123456789"),
    )

    assert client.send_text_calls
    assert client.typing_calls == []
