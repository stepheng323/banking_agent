from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from apps.gateway.api.webhooks.telegram import router as router_module
from apps.gateway.api.webhooks.telegram.router import (
    BvnInput,
    LinkingMethodInput,
    PinSubmitInput,
    TelegramBootstrapInput,
)
from shared.cache.flow_session_manager import SessionReadResult
from shared.services.auth.authorization import AuthorizationResult
from shared.services.channel_linking import ChannelLinkPinResult
from shared.services.telegram_miniapp_bootstrap import TelegramMiniAppBootstrap


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


class _BvnServiceStub:
    def __init__(self, session: dict[str, Any] | None) -> None:
        self.session = session
        self.initiate_calls: list[tuple[str, str]] = []
        self.send_otp_calls: list[tuple[str, str]] = []

    async def get_session_data(self, flow_token: str) -> dict[str, Any] | None:
        del flow_token
        return self.session

    async def get_session_status(self, flow_token: str) -> SessionReadResult:
        del flow_token
        if self.session is None:
            return SessionReadResult(status="missing")
        return SessionReadResult(status="found", data=self.session)

    async def initiate_bvn_verification(self, flow_token: str, bvn: str) -> dict[str, Any]:
        self.initiate_calls.append((flow_token, bvn))
        return {"success": True}

    async def send_otp(self, flow_token: str, method: str) -> dict[str, Any]:
        self.send_otp_calls.append((flow_token, method))
        return {"success": True}


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

    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "telegram-secret")
    monkeypatch.setattr(router_module.QueuePublisherFactory, "get_publisher", lambda: _PublisherStub())
    monkeypatch.setattr(router_module, "TelegramWebhookService", _FailingService)
    monkeypatch.setattr(router_module, "logger", logger)

    response = await router_module.telegram_webhook(
        request=_RequestStub(payload),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        x_telegram_bot_api_secret_token="telegram-secret",
    )

    assert response.status_code == 200
    assert db.commits == 0
    assert db.rollbacks == 1
    assert (
        "telegram_webhook_error_acknowledged",
        {
            "error": "Temporary failure in name resolution",
            "error_type": "OSError",
            "retry_suppressed": True,
            "acknowledged": True,
            "delivery_policy": "drop_on_failure_no_retry",
            "exc_info": True,
        },
    ) in logger.events


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

    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "telegram-secret")
    monkeypatch.setattr(router_module.QueuePublisherFactory, "get_publisher", lambda: _PublisherStub())
    monkeypatch.setattr(router_module, "TelegramWebhookService", _SuccessfulService)
    monkeypatch.setattr(router_module, "logger", logger)

    response = await router_module.telegram_webhook(
        request=_RequestStub(payload),  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        x_telegram_bot_api_secret_token="telegram-secret",
    )

    assert response.status_code == 200
    assert db.commits == 1
    assert db.rollbacks == 0
    assert ("telegram_webhook_processed", {"update_id": 321, "handled": True}) in logger.events


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_invalid_secret_without_logging_raw_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"update_id": 321, "message": {"message_id": 77, "chat": {"id": 12345}, "text": "hi"}}
    db = _DbStub()
    logger = _LoggerStub()

    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "telegram-secret")
    monkeypatch.setattr(router_module, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await router_module.telegram_webhook(
            request=_RequestStub(payload),  # type: ignore[arg-type]
            db=db,  # type: ignore[arg-type]
            x_telegram_bot_api_secret_token="attacker-token",
        )

    assert exc_info.value.status_code == 401
    assert db.commits == 0
    assert db.rollbacks == 0
    assert len(logger.events) == 1
    event, fields = logger.events[0]
    assert event == "telegram_webhook_unauthorized"
    assert fields["provided_token_present"] is True
    assert fields["provided_token_hash"] == router_module._token_fingerprint("attacker-token")
    assert "attacker-token" not in str(fields)
    assert "telegram-secret" not in str(fields)


@pytest.mark.asyncio
async def test_telegram_webhook_missing_secret_fails_closed_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"update_id": 321, "message": {"message_id": 77, "chat": {"id": 12345}, "text": "hi"}}
    db = _DbStub()
    logger = _LoggerStub()

    monkeypatch.setattr(router_module.settings, "telegram_webhook_secret_token", "")
    monkeypatch.setattr(router_module.settings.runtime, "app_env", "production")
    monkeypatch.setattr(router_module, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await router_module.telegram_webhook(
            request=_RequestStub(payload),  # type: ignore[arg-type]
            db=db,  # type: ignore[arg-type]
            x_telegram_bot_api_secret_token=None,
        )

    assert exc_info.value.status_code == 401
    assert db.commits == 0
    assert db.rollbacks == 0
    assert len(logger.events) == 1
    event, fields = logger.events[0]
    assert event == "telegram_webhook_unauthorized"
    assert fields["provided_token_present"] is False
    assert fields["provided_token_hash"] == ""
    assert fields["expected_token_configured"] is False


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
            assert pin == "123456"
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
        PinSubmitInput(flow_token="transaction-pin-idem-1-927331985", pin="123456", chat_id="927331985"),
        user_data={"user": '{"id": 927331985}'},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": True}
    assert len(publisher.published) == 1
    topic, message = publisher.published[0]
    assert topic == "flow_event.process"
    assert message["idempotency_key"] == "idem-1"
    assert message["extra_data"] == {"source": "telegram_mini_app_rest", "chat_id": "927331985"}
    assert "pin" not in message["extra_data"]


@pytest.mark.asyncio
async def test_telegram_bootstrap_returns_server_side_token_for_matching_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _consume(**kwargs: Any) -> TelegramMiniAppBootstrap:
        assert kwargs == {"nonce": "nonce-1", "endpoint": "pin", "init_user_id": "927331985"}
        return TelegramMiniAppBootstrap(
            flow_token="transfer-pin-idem-1-927331985",
            chat_id="927331985",
            endpoint="pin",
            extra={"header": "Authorize transfer", "body_text": "Enter PIN"},
        )

    monkeypatch.setattr(router_module, "consume_telegram_miniapp_bootstrap", _consume)

    result = await router_module.telegram_bootstrap(
        TelegramBootstrapInput(boot="nonce-1", endpoint="pin"),
        user_data={"user": '{"id": 927331985}'},
    )

    assert result == {
        "success": True,
        "flow_token": "transfer-pin-idem-1-927331985",
        "chat_id": "927331985",
        "header": "Authorize transfer",
        "body_text": "Enter PIN",
    }


@pytest.mark.asyncio
async def test_telegram_bootstrap_rejects_missing_or_replayed_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _consume(**kwargs: Any) -> None:
        del kwargs
        return None

    monkeypatch.setattr(router_module, "consume_telegram_miniapp_bootstrap", _consume)

    result = await router_module.telegram_bootstrap(
        TelegramBootstrapInput(boot="expired", endpoint="onboarding"),
        user_data={"user": '{"id": 927331985}'},
    )

    assert result == {"success": False, "error": "Invalid or expired session. Please reopen this page from Telegram."}


@pytest.mark.asyncio
async def test_telegram_pin_submit_requires_init_user_for_transaction_pin() -> None:
    result = await router_module.telegram_pin_submit(
        PinSubmitInput(flow_token="transaction-pin-idem-1-927331985", pin="123456", chat_id="927331985"),
        user_data={},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": False, "error": "Telegram authentication missing. Reopen this page from Telegram."}


@pytest.mark.asyncio
async def test_telegram_pin_submit_rejects_mismatched_chat_id_for_transaction_pin() -> None:
    result = await router_module.telegram_pin_submit(
        PinSubmitInput(flow_token="transaction-pin-idem-1-927331985", pin="123456", chat_id="attacker-chat"),
        user_data={"user": '{"id": 927331985}'},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": False, "error": "This PIN request is not valid for this Telegram account."}


@pytest.mark.asyncio
async def test_telegram_pin_submit_rejects_flow_token_bound_to_other_chat_id() -> None:
    result = await router_module.telegram_pin_submit(
        PinSubmitInput(flow_token="transaction-pin-idem-1-111111", pin="123456", chat_id="927331985"),
        user_data={"user": '{"id": 927331985}'},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": False, "error": "This PIN request is not valid for this Telegram account."}


@pytest.mark.asyncio
async def test_telegram_pin_submit_completes_channel_link_with_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _complete_channel_link_with_pin(**kwargs: Any) -> ChannelLinkPinResult:
        calls.append(kwargs)
        return ChannelLinkPinResult(
            success=True,
            status="success",
            requested_channel="whatsapp",
            requested_channel_user_id="2348162511023",
        )

    class _WhatsAppClientStub:
        def __init__(self) -> None:
            self.text_calls: list[dict[str, Any]] = []

        async def send_text(self, **kwargs: Any) -> dict[str, Any]:
            self.text_calls.append(kwargs)
            return {"ok": True}

    whatsapp_client = _WhatsAppClientStub()
    monkeypatch.setattr(router_module, "complete_channel_link_with_pin", _complete_channel_link_with_pin)
    monkeypatch.setattr(router_module, "WhatsAppClient", lambda: whatsapp_client)
    monkeypatch.setattr("shared.cache.redis_client.RedisClient.get_client", lambda: _RedisStub())

    result = await router_module.telegram_pin_submit(
        PinSubmitInput(
            flow_token="channel-link-pin-channel-link-token",
            pin="123456",
            chat_id="927331985",
        ),
        user_data={"user": '{"id": 927331985}'},
        db=_DbStub(),  # type: ignore[arg-type]
    )

    assert result == {"success": True}
    assert calls == [
        {
            "flow_token": "channel-link-pin-channel-link-token",
            "pin": "123456",
            "authorizing_channel": "telegram",
            "authorizing_channel_user_id": "927331985",
        }
    ]
    assert whatsapp_client.text_calls == [
        {
            "to": "2348162511023",
            "text": "Your WhatsApp number has been linked. You can now use banking features there.",
            "suppress_typing_indicator": True,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_onboarding_bvn_rejects_wrong_session_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_user_id": "927331985",
            "step": "bvn_entry",
        }
    )
    monkeypatch.setattr(router_module, "bvn_service", bvn_stub)

    result = await router_module.telegram_onboarding_bvn(
        BvnInput(flow_token="onboarding-opaque-token", bvn="12345678901"),
        user_data={"user": '{"id": 111111}'},
    )

    assert result == {"success": False, "error": "Invalid or expired session. Please reopen this page from Telegram."}
    assert bvn_stub.initiate_calls == []


@pytest.mark.asyncio
async def test_telegram_linking_method_rejects_wrong_session_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "phone_number": "2348162511023",
            "bvn": "12345678901",
            "session_id": "mono-session",
            "methods": [{"id": "sms", "title": "081***1023"}],
            "is_account_linking": True,
            "channel": "telegram",
            "channel_user_id": "927331985",
            "step": "method_selection",
        }
    )
    monkeypatch.setattr(router_module, "bvn_service", bvn_stub)

    result = await router_module.telegram_linking_method(
        LinkingMethodInput(flow_token="link-opaque-token", method="sms"),
        user_data={"user": '{"id": 111111}'},
    )

    assert result == {"success": False, "error": "Invalid or expired session. Please reopen this page from Telegram."}
    assert bvn_stub.send_otp_calls == []


def test_telegram_mini_apps_do_not_interpolate_provider_rows_with_innerhtml() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative_path in (
        "apps/gateway/static/telegram/onboarding.html",
        "apps/gateway/static/telegram/linking.html",
    ):
        text = (root / relative_path).read_text()
        assert "label.innerHTML" not in text


def test_telegram_mini_apps_bootstrap_without_query_flow_tokens() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative_path in (
        "apps/gateway/static/telegram/onboarding.html",
        "apps/gateway/static/telegram/linking.html",
        "apps/gateway/static/telegram/pin_entry.html",
    ):
        text = (root / relative_path).read_text()
        assert 'params.get("flow_token")' not in text
        assert 'params.get("chat_id")' not in text
        assert "/webhook/telegram/bootstrap" in text


def test_telegram_pin_surfaces_require_six_digit_transaction_pin() -> None:
    root = Path(__file__).resolve().parents[2]
    onboarding = (root / "apps/gateway/static/telegram/onboarding.html").read_text()
    pin_entry = (root / "apps/gateway/static/telegram/pin_entry.html").read_text()
    whatsapp_flow = (root / "config/whatsapp_pin_flow.json").read_text()

    assert 'placeholder="Transaction PIN (6 digits)"' in onboarding
    assert "const PIN_LENGTH = 6" in onboarding
    assert "pin.length === PIN_LENGTH" in onboarding
    assert "Use your 6-digit transaction PIN" in pin_entry
    assert "const PIN_LENGTH = 6" in pin_entry
    assert '"min-chars": 6' in whatsapp_flow
    assert '"max-chars": 6' in whatsapp_flow
