from typing import Any

import pytest

from apps.gateway.api.webhooks.telegram import router as router_module


class _RequestStub:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class _DbStub:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _LoggerStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


class _PublisherStub:
    pass


@pytest.mark.asyncio
async def test_telegram_webhook_acknowledges_failure_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"update_id": 123, "message": {"message_id": 99, "chat": {"id": 12345}, "text": "hi"}}
    db = _DbStub()
    logger = _LoggerStub()

    class _FailingService:
        def __init__(self, **_: Any) -> None:
            pass

        async def process_update(self, update: dict[str, Any]) -> bool:
            assert update == payload
            raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr(router_module, "require_webhook_ingress_enabled", lambda _: None)
    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "")
    monkeypatch.setattr(router_module.QueuePublisherFactory, "get_publisher", lambda: _PublisherStub())
    monkeypatch.setattr(router_module, "TelegramWebhookService", _FailingService)
    monkeypatch.setattr(router_module, "logger", logger)

    response = await router_module.telegram_webhook(
        request=_RequestStub(payload),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        x_telegram_bot_api_secret_token=None,
    )

    assert response.status_code == 200
    assert db.commits == 0
    assert db.rollbacks == 1
    assert ("telegram_webhook_error_acknowledged", {
        "error": "Temporary failure in name resolution",
        "error_type": "OSError",
        "retry_suppressed": True,
        "acknowledged": True,
        "delivery_policy": "drop_on_failure_no_retry",
        "exc_info": True,
    }) in logger.events


@pytest.mark.asyncio
async def test_telegram_webhook_commits_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"update_id": 321, "message": {"message_id": 77, "chat": {"id": 12345}, "text": "hi"}}
    db = _DbStub()
    logger = _LoggerStub()

    class _SuccessfulService:
        def __init__(self, **_: Any) -> None:
            pass

        async def process_update(self, update: dict[str, Any]) -> bool:
            assert update == payload
            return True

    monkeypatch.setattr(router_module, "require_webhook_ingress_enabled", lambda _: None)
    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "")
    monkeypatch.setattr(router_module.QueuePublisherFactory, "get_publisher", lambda: _PublisherStub())
    monkeypatch.setattr(router_module, "TelegramWebhookService", _SuccessfulService)
    monkeypatch.setattr(router_module, "logger", logger)

    response = await router_module.telegram_webhook(
        request=_RequestStub(payload),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        x_telegram_bot_api_secret_token=None,
    )

    assert response.status_code == 200
    assert db.commits == 1
    assert db.rollbacks == 0
    assert ("telegram_webhook_processed", {"update_id": 321, "handled": True}) in logger.events
