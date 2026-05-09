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
    def __init__(self) -> None:
        self.channel_identity_calls = 0

    async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        self.channel_identity_calls += 1
        return SimpleNamespace(id="user-1")


class _RedisStub:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.values[key] = value


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
    user_repository = _UserRepositoryStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=user_repository,
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
    assert user_repository.channel_identity_calls == 1


@pytest.mark.asyncio
async def test_process_update_uses_cached_linked_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_stub = _RedisStub(
        {
            "cache:channel_identity:telegram:12345": (
                '{"id": "user-1", "phone_number": "2348162511023", '
                '"onboarding_status": "onboarding_completed", "full_name": "Gaines"}'
            )
        }
    )
    monkeypatch.setattr("shared.cache.channel_identity_cache.RedisClient.get_client", lambda: redis_stub)
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    user_repository = _UserRepositoryStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=user_repository,
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "Hello",
            },
        }
    )

    assert handled is True
    assert len(publisher.published) == 1
    assert user_repository.channel_identity_calls == 0
