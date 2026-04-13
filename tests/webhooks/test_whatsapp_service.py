from typing import Any

import pytest

from apps.gateway.adapters.meta_whatsapp import ParsedMessage
from apps.gateway.api.webhooks.whatsapp.message.service import WhatsAppWebhookService


class _PublisherStub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append((topic, message))


class _WhatsAppClientStub:
    pass


@pytest.mark.asyncio
async def test_process_message_allows_hardcoded_local_number() -> None:
    publisher = _PublisherStub()
    service = WhatsAppWebhookService(
        publisher=publisher,  # type: ignore[arg-type]
        whatsapp_client=_WhatsAppClientStub(),  # type: ignore[arg-type]
    )

    handled = await service._process_message(
        ParsedMessage.model_validate(
            {
                "id": "wamid-1",
                "from": "08162511023",
                "type": "text",
                "text": "hi",
            }
        )
    )

    assert handled is True
    assert len(publisher.published) == 1


@pytest.mark.asyncio
async def test_process_message_allows_hardcoded_number_in_meta_format() -> None:
    publisher = _PublisherStub()
    service = WhatsAppWebhookService(
        publisher=publisher,  # type: ignore[arg-type]
        whatsapp_client=_WhatsAppClientStub(),  # type: ignore[arg-type]
    )

    handled = await service._process_message(
        ParsedMessage.model_validate(
            {
                "id": "wamid-1",
                "from": "2348162511023",
                "type": "text",
                "text": "hi",
            }
        )
    )

    assert handled is True
    assert len(publisher.published) == 1


@pytest.mark.asyncio
async def test_process_message_blocks_non_allowed_number() -> None:
    publisher = _PublisherStub()
    service = WhatsAppWebhookService(
        publisher=publisher,  # type: ignore[arg-type]
        whatsapp_client=_WhatsAppClientStub(),  # type: ignore[arg-type]
    )

    handled = await service._process_message(
        ParsedMessage.model_validate(
            {
                "id": "wamid-1",
                "from": "08000000000",
                "type": "text",
                "text": "hi",
            }
        )
    )

    assert handled is False
    assert publisher.published == []
