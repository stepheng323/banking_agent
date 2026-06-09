import pytest

from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from shared.messaging.channels import MessagingChannel, normalize_messaging_channel


def test_normalize_messaging_channel_accepts_supported_strings() -> None:
    assert normalize_messaging_channel("whatsapp") is MessagingChannel.WHATSAPP
    assert normalize_messaging_channel("TELEGRAM") is MessagingChannel.TELEGRAM


def test_normalize_messaging_channel_rejects_unknown_channel() -> None:
    with pytest.raises(ValueError):
        normalize_messaging_channel("sms")


def test_message_context_normalizes_channel_string() -> None:
    context = MessageContext(
        phone_number="2348000000000",
        text="hello",
        message_id="msg-1",
        channel="telegram",  # type: ignore[arg-type]
    )

    assert context.channel is MessagingChannel.TELEGRAM
