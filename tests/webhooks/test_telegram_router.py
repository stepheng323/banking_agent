from typing import Any

import pytest

from apps.gateway.api.webhooks.telegram import router as router_module
from apps.gateway.api.webhooks.telegram.router import PinSubmitInput
from shared.services.auth.authorization import AuthorizationResult


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
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append((topic, message))


class _RedisStub:
    async def get(self, key: str) -> str | None:
        if key == "transaction:token:idem-1:phone":
            return "2348162511023"
        return None

    async def getdel(self, key: str) -> None:
        del key
        return None


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


@pytest.mark.asyncio
async def test_telegram_pin_submit_does_not_publish_plaintext_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    publisher = _PublisherStub()

    class _AuthorizationServiceStub:
        def __init__(self, redis_client: Any) -> None:
            self.redis_client = redis_client

        async def verify_pin(
            self,
            phone_number: str,
            pin: str,
            idempotency_key: str,
            transaction_type: str | None = None,
        ) -> AuthorizationResult:
            assert phone_number == "2348162511023"
            assert pin == "1234"
            assert idempotency_key == "idem-1"
            assert transaction_type == "transaction"
            return AuthorizationResult(
                verified=True,
                user_id="user-1",
                transaction_type="transfer",
            )

        async def store_pin_verification_result(
            self,
            idempotency_key: str,
            result: AuthorizationResult,
        ) -> None:
            assert idempotency_key == "idem-1"
            assert result.verified is True

    monkeypatch.setattr("shared.cache.redis_client.RedisClient.get_client", lambda: _RedisStub())
    monkeypatch.setattr("shared.services.auth.authorization.AuthorizationService", _AuthorizationServiceStub)
    monkeypatch.setattr(router_module.QueuePublisherFactory, "get_publisher", lambda: publisher)

    result = await router_module.telegram_pin_submit(
        PinSubmitInput(flow_token="transaction-pin-idem-1-927331985", pin="1234", chat_id="927331985"),
        user_data={},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": True}
    assert len(publisher.published) == 1
    topic, message = publisher.published[0]
    assert topic == "flow_event.process"
    assert message["idempotency_key"] == "idem-1"
    assert message["extra_data"] == {"source": "telegram_mini_app_rest", "chat_id": "927331985"}
    assert "pin" not in message["extra_data"]
