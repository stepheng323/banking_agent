from types import SimpleNamespace
from typing import Any

import pytest

from apps.gateway.api.webhooks.telegram.service import TelegramWebhookService


class _PublisherStub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append((topic, message))


class _UserRepositoryStub:
    async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        return SimpleNamespace(id="user-1")


class _TelegramClientStub:
    def __init__(self) -> None:
        self.typing_calls: list[str] = []
        self.text_calls: list[dict[str, str]] = []

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True

    async def send_text(self, *, to: str, text: str) -> dict[str, Any]:
        self.text_calls.append({"to": to, "text": text})
        return {"ok": True}


@pytest.mark.asyncio
async def test_process_update_enqueues_linked_text_without_eager_typing() -> None:
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=_UserRepositoryStub(),
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "How much did I send to mum this week",
            },
        }
    )

    assert handled is True
    assert telegram_client.typing_calls == []
    assert telegram_client.text_calls == []
    assert len(publisher.published) == 1
    topic, payload = publisher.published[0]
    assert topic == "message.received"
    assert payload["message_id"] == "99"
    assert payload["channel_user_id"] == "12345"
    assert payload["message_type"] == "text"
    assert payload["text"] == "How much did I send to mum this week"
    assert payload["channel"] == "telegram"
