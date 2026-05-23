from typing import Any, cast

import pytest

from apps.chat.src.agent.orchestrator.models.intents import RequestAuth, RequestConfirmation
from apps.chat.src.messaging.presenters.base import PresentationContext
from apps.chat.src.messaging.presenters.whatsapp import WhatsAppPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient


class _StubFlowWhatsAppClient:
    def __init__(self) -> None:
        self.flow_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    async def send_flow(self, **kwargs: Any) -> MessageResult:
        self.flow_calls.append(kwargs)
        return MessageResult(success=True, message_id="wa-flow-msg-1")

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"messages": [{"id": "wa-text-msg-1"}]}


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
