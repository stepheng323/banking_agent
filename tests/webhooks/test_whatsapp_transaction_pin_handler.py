import json
from typing import Any

import pytest

from apps.gateway.api.webhooks.whatsapp.flows.handlers import transaction_pin_handler as handler_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers.transaction_pin_handler import (
    handle_transaction_pin,
    parse_transaction_pin_flow_token,
)
from shared.services.auth.authorization import AuthorizationResult


class _WhatsAppClientStub:
    channel_name = "whatsapp"


class _RedisStub:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class _AuthorizationServiceStub:
    def __init__(self, result: AuthorizationResult) -> None:
        self.result = result
        self.verify_calls: list[tuple[str, str, str, str | None]] = []
        self.stored: list[tuple[str, AuthorizationResult]] = []

    async def verify_pin(
        self,
        phone_number: str,
        pin: str,
        idempotency_key: str,
        transaction_type: str | None = None,
    ) -> AuthorizationResult:
        self.verify_calls.append((phone_number, pin, idempotency_key, transaction_type))
        return self.result

    async def store_pin_verification_result(self, idempotency_key: str, result: AuthorizationResult) -> None:
        self.stored.append((idempotency_key, result))


class _PublisherStub:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append({"topic": topic, "message": message})


def test_parse_transaction_pin_flow_token_handles_hyphenated_idempotency_keys() -> None:
    parsed = parse_transaction_pin_flow_token("transfer-pin-transfer-abc-123-2348162511023")

    assert parsed is not None
    assert parsed.transaction_type == "transfer"
    assert parsed.idempotency_key == "transfer-abc-123"
    assert parsed.phone_hint == "2348162511023"


def test_parse_transaction_pin_flow_token_accepts_transaction_prefix_without_type() -> None:
    parsed = parse_transaction_pin_flow_token("transaction-pin-idem-1-2348162511023")

    assert parsed is not None
    assert parsed.transaction_type is None
    assert parsed.idempotency_key == "idem-1"
    assert parsed.phone_hint == "2348162511023"


@pytest.mark.asyncio
async def test_whatsapp_transaction_pin_rejects_missing_flow_token() -> None:
    response = await handle_transaction_pin(
        {"pin": "1234"},
        "",
        False,
        b"",
        b"",
        _WhatsAppClientStub(),  # type: ignore[arg-type]
        publisher=_PublisherStub(),
    )

    body = json.loads(response.body)
    assert body == {
        "version": "3.0",
        "screen": "Pin",
        "data": {
            "show_error": True,
            "error_message": "Invalid transaction session. Please start a new transaction.",
        },
    }


@pytest.mark.asyncio
async def test_whatsapp_transaction_pin_rejects_unknown_flow_token() -> None:
    response = await handle_transaction_pin(
        {"pin": "1234"},
        "unknown-pin-token",
        False,
        b"",
        b"",
        _WhatsAppClientStub(),  # type: ignore[arg-type]
        publisher=_PublisherStub(),
    )

    body = json.loads(response.body)
    assert body["screen"] == "Pin"
    assert body["data"]["show_error"] is True
    assert body["data"]["error_message"] == "Invalid transaction session. Please start a new transaction."


@pytest.mark.asyncio
async def test_whatsapp_transaction_pin_success_does_not_echo_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _RedisStub({"transfer:token:idem-1:phone": "2348162511023"})
    auth_service = _AuthorizationServiceStub(
        AuthorizationResult(verified=True, user_id="user-1", transaction_type="transfer")
    )
    publisher = _PublisherStub()

    monkeypatch.setattr(handler_module.RedisClient, "get_client", staticmethod(lambda: redis))
    monkeypatch.setattr(handler_module, "AuthorizationService", lambda redis_client=None: auth_service)

    response = await handle_transaction_pin(
        {"pin": "1234"},
        "transfer-pin-idem-1-2348162511023",
        False,
        b"",
        b"",
        _WhatsAppClientStub(),  # type: ignore[arg-type]
        publisher=publisher,
    )

    body = json.loads(response.body)
    params = body["data"]["extension_message_response"]["params"]
    assert body["screen"] == "SUCCESS"
    assert params == {
        "flow_token": "transfer-pin-idem-1-2348162511023",
        "success": "true",
    }
    assert "pin" not in params
    assert auth_service.verify_calls == [("2348162511023", "1234", "idem-1", "transfer")]
    assert auth_service.stored == [("idem-1", auth_service.result)]
    assert publisher.published[0]["message"]["event_type"] == "pin_verified"
