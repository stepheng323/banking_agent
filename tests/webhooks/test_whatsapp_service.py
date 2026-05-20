from typing import Any

import pytest

from apps.gateway.adapters.meta_whatsapp import ParsedMessage, parse_payload
from apps.gateway.api.webhooks.whatsapp.message import service as service_module
from apps.gateway.api.webhooks.whatsapp.message.service import WhatsAppWebhookService


class _PublisherStub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append((topic, message))


class _WhatsAppClientStub:
    def __init__(self) -> None:
        self.text_calls: list[dict[str, Any]] = []

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"ok": True}


class _SessionManagerStub:
    def __init__(self, sessions: dict[str, dict[str, Any]] | None = None) -> None:
        self.sessions = sessions or {}
        self.deleted: list[str] = []

    async def get_session(self, flow_token: str) -> dict[str, Any]:
        return self.sessions.get(flow_token, {})

    async def delete_session(self, flow_token: str) -> None:
        self.deleted.append(flow_token)
        self.sessions.pop(flow_token, None)


def test_parse_payload_preserves_whatsapp_image_caption_media_and_mime() -> None:
    parsed = parse_payload(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid-image",
                                        "from": "2348162511023",
                                        "type": "image",
                                        "image": {
                                            "id": "media-image",
                                            "mime_type": "image/png",
                                            "caption": "send 5k for groceries",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )

    assert len(parsed) == 1
    assert parsed[0].type == "image"
    assert parsed[0].text == "send 5k for groceries"
    assert parsed[0].media_id == "media-image"
    assert parsed[0].mime_type == "image/png"


def test_parse_payload_maps_image_document_to_image_media() -> None:
    parsed = parse_payload(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid-doc-image",
                                        "from": "2348162511023",
                                        "type": "document",
                                        "document": {
                                            "id": "media-doc",
                                            "mime_type": "image/jpeg",
                                            "caption": "use this account",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )

    assert len(parsed) == 1
    assert parsed[0].type == "image"
    assert parsed[0].text == "use this account"
    assert parsed[0].media_id == "media-doc"
    assert parsed[0].mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_process_message_allows_configured_local_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings.whatsapp, "allowed_numbers", {"08162511023"})
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
async def test_process_message_allows_configured_number_in_meta_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings.whatsapp, "allowed_numbers", {"08162511023"})
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
async def test_process_message_blocks_non_allowed_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings.whatsapp, "allowed_numbers", {"08162511023"})
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


@pytest.mark.asyncio
async def test_process_message_allows_all_when_no_allowlist_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings.whatsapp, "allowed_numbers", set())
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

    assert handled is True
    assert len(publisher.published) == 1


@pytest.mark.asyncio
async def test_stale_whatsapp_button_payload_does_not_link_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings.whatsapp, "allowed_numbers", set())
    publisher = _PublisherStub()
    whatsapp_client = _WhatsAppClientStub()
    service = WhatsAppWebhookService(
        publisher=publisher,  # type: ignore[arg-type]
        whatsapp_client=whatsapp_client,  # type: ignore[arg-type]
    )

    handled = await service._process_message(
        ParsedMessage.model_validate(
            {
                "id": "wamid-approval",
                "from": "2348162511023",
                "type": "interactive",
                "text": "ch_link_ok:channel-link-opaque-token",
            }
        )
    )

    assert handled is True
    assert publisher.published == []
    assert "requires PIN authorization" in whatsapp_client.text_calls[-1]["text"]
