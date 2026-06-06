from typing import Any, cast

import pytest

from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.messaging.intents import RequestAuth, RequestConfirmation, SendTyping
from shared.messaging.presenters.base import PresentationContext
from shared.messaging.presenters.whatsapp import WhatsAppPresenter


class _StubFlowWhatsAppClient:
    def __init__(self) -> None:
        self.flow_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []
        self.typing_calls: list[str] = []

    async def send_flow(self, **kwargs: Any) -> MessageResult:
        self.flow_calls.append(kwargs)
        return MessageResult(success=True, message_id="wa-flow-msg-1")

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"messages": [{"id": "wa-text-msg-1"}]}

    async def send_typing_indicator(self, message_id: str) -> dict[str, Any]:
        self.typing_calls.append(message_id)
        return {}


@pytest.mark.asyncio
async def test_whatsapp_presenter_confirmation_uses_context_header() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
    intent = RequestConfirmation(
        task_ids=["t1"],
        summary="*₦5,000 → Tolu*",
        token="tok-1",
        correlation_id="corr-1",
        header="Confirm Transfer",
    )
    context = PresentationContext(
        channel="whatsapp",
        phone_number="123456789",
        capabilities={"flows": True},
    )

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "wa-flow-msg-1"
    assert len(client.flow_calls) == 1
    flow_config = client.flow_calls[0]["flow_config"]
    assert flow_config["header"] == "Confirm Transfer"
    assert flow_config["flow_token"] == "transfer-pin-corr-1-123456789"
    assert "flow_action" not in flow_config
    assert flow_config["flow_action_payload"] == {"screen": "Pin"}


@pytest.mark.asyncio
async def test_whatsapp_presenter_send_typing_uses_inbound_message_id() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))

    await presenter.present(
        [SendTyping()],
        PresentationContext(
            channel="whatsapp",
            phone_number="123456789",
            metadata={"inbound_message_id": "wamid.inbound"},
        ),
    )

    assert client.typing_calls == ["wamid.inbound"]


@pytest.mark.asyncio
async def test_whatsapp_presenter_send_typing_falls_back_to_message_id() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))

    await presenter.present(
        [SendTyping()],
        PresentationContext(
            channel="whatsapp",
            phone_number="123456789",
            metadata={"message_id": "wamid.message"},
        ),
    )

    assert client.typing_calls == ["wamid.message"]


@pytest.mark.asyncio
async def test_whatsapp_presenter_send_typing_noops_without_message_id() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))

    await presenter.present(
        [SendTyping()],
        PresentationContext(channel="whatsapp", phone_number="123456789"),
    )

    assert client.typing_calls == []


@pytest.mark.asyncio
async def test_whatsapp_presenter_schedule_update_confirmation_uses_text_not_pin_flow() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
    intent = RequestConfirmation(
        task_ids=["t1"],
        summary="Transfer: ₦20,000 Mum • One Time at 9:00 AM WAT",
        token="tok-1",
        correlation_id="corr-1",
        header="Confirm Schedule Update",
    )
    intent.actionable_payload = {"task_type": "schedule", "action": "edit_scheduled_transaction"}
    context = PresentationContext(
        channel="whatsapp",
        phone_number="123456789",
        capabilities={"flows": True},
    )

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "wa-text-msg-1"
    assert client.flow_calls == []
    assert client.text_calls[0]["text"].startswith("Confirm Schedule Update")
    assert "Reply yes to confirm" in client.text_calls[0]["text"]


@pytest.mark.asyncio
async def test_whatsapp_presenter_schedule_auth_uses_schedule_pin_prefix() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
    intent = RequestAuth(
        method="pin",
        task_ids=["t1"],
        correlation_id="corr-1",
        reason="Authorize Schedule Update",
        summary="Confirm schedule update",
    )
    intent.actionable_payload = {"task_type": "schedule", "action": "edit_scheduled_transaction"}
    context = PresentationContext(
        channel="whatsapp",
        phone_number="123456789",
        capabilities={"flows": True},
    )

    message_id = await presenter._present_auth(intent, context)

    assert message_id == "wa-flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "schedule-pin-corr-1-123456789"
    assert client.flow_calls[0]["flow_config"]["flow_cta"] == "Authorize Update"


@pytest.mark.asyncio
async def test_whatsapp_presenter_batch_confirmation_uses_prefix_for_correlation_task() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
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
    context = PresentationContext(
        channel="whatsapp",
        phone_number="123456789",
        capabilities={"flows": True},
    )

    message_id = await presenter._present_confirmation(intent, context)

    assert message_id == "wa-flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "transfer-pin-idem-transfer-123456789"


@pytest.mark.asyncio
async def test_whatsapp_presenter_batch_auth_uses_prefix_for_correlation_task() -> None:
    client = _StubFlowWhatsAppClient()
    presenter = WhatsAppPresenter(cast(MessagingClient, client))
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
    context = PresentationContext(
        channel="whatsapp",
        phone_number="123456789",
        capabilities={"flows": True},
    )

    message_id = await presenter._present_auth(intent, context)

    assert message_id == "wa-flow-msg-1"
    assert client.flow_calls[0]["flow_config"]["flow_token"] == "data-pin-idem-data-123456789"
