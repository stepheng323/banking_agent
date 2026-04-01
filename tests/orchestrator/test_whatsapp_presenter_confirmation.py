from typing import Any, cast

import pytest

from apps.core.src.agent.orchestrator.models.intents import RequestConfirmation
from apps.core.src.messaging.presenters.base import PresentationContext
from apps.core.src.messaging.presenters.whatsapp import WhatsAppPresenter
from shared.clients.abstractions.messaging import MessageResult, MessagingClient


class _StubFlowWhatsAppClient:
    def __init__(self) -> None:
        self.flow_calls: list[dict[str, Any]] = []

    async def send_flow(self, **kwargs: Any) -> MessageResult:
        self.flow_calls.append(kwargs)
        return MessageResult(success=True, message_id="wa-flow-msg-1")


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
    assert client.flow_calls[0]["flow_config"]["header"] == "Confirm Transfer"
