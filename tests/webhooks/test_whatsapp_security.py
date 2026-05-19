import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

import pytest
from starlette.requests import Request

from apps.gateway.adapters import meta_whatsapp
from apps.gateway.adapters.meta_whatsapp import WebhookSignatureError, verify_meta_signature
from apps.gateway.api.webhooks.whatsapp.flows import request_processor
from apps.gateway.api.webhooks.whatsapp.flows.request_processor import process_flow_request
from apps.gateway.api.webhooks.whatsapp.message.router import whatsapp_webhook
from shared.config.settings import Settings
from shared.utils.flow_decryption import is_encrypted


def _request(body: bytes, headers: Mapping[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": raw_headers,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _json_request(payload: dict[str, Any], headers: Mapping[str, str] | None = None) -> Request:
    body = json.dumps(payload).encode("utf-8")
    merged_headers = {"content-type": "application/json", **(headers or {})}
    return _request(body, merged_headers)


@pytest.mark.asyncio
async def test_verify_meta_signature_accepts_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"entry":[]}'
    secret = "test-secret"
    digest = hmac.new(secret.encode("utf-8"), body, digestmod=hashlib.sha256).hexdigest()
    monkeypatch.setattr(meta_whatsapp.settings.whatsapp, "app_secret", secret)

    await verify_meta_signature(_request(body, {"X-Hub-Signature-256": f"sha256={digest}"}))


@pytest.mark.asyncio
async def test_verify_meta_signature_rejects_missing_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meta_whatsapp.settings.whatsapp, "app_secret", "test-secret")

    with pytest.raises(WebhookSignatureError, match="Missing or invalid signature header"):
        await verify_meta_signature(_request(b'{"entry":[]}'))


@pytest.mark.asyncio
async def test_whatsapp_webhook_returns_403_for_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meta_whatsapp.settings.whatsapp, "app_secret", "test-secret")

    response = await whatsapp_webhook(
        _request(b'{"entry":[]}', {"X-Hub-Signature-256": "sha256=bad"})
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_process_flow_request_rejects_plaintext_when_app_env_is_non_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "production")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "dev")

    processed, error = await process_flow_request(
        _json_request({"screen": "BVN_ENTRY", "data": {"bvn": "12345678901"}, "flow_token": "token"})
    )

    assert processed is None
    assert error is not None
    assert error.status_code == 400


@pytest.mark.asyncio
async def test_process_flow_request_allows_plaintext_in_local_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    processed, error = await process_flow_request(
        _json_request({"screen": "BVN_ENTRY", "data": {"bvn": "12345678901"}, "flow_token": "token"})
    )

    assert error is None
    assert processed is not None
    assert processed.screen == "BVN_ENTRY"
    assert processed.request_was_encrypted is False


@pytest.mark.asyncio
async def test_process_flow_request_extracts_provider_whatsapp_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    processed, error = await process_flow_request(
        _json_request(
            {
                "screen": "Pin",
                "contacts": [{"wa_id": "2348162511023"}],
                "data": {"pin": "1234", "wa_id": "0000000000"},
                "flow_token": "channel-link-pin-channel-link-token",
            }
        )
    )

    assert error is None
    assert processed is not None
    assert processed.authorizing_channel_user_id == "2348162511023"


@pytest.mark.asyncio
async def test_process_flow_request_does_not_trust_user_controlled_data_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    processed, error = await process_flow_request(
        _json_request(
            {
                "screen": "Pin",
                "data": {"pin": "1234", "wa_id": "2348162511023"},
                "flow_token": "channel-link-pin-channel-link-token",
            }
        )
    )

    assert error is None
    assert processed is not None
    assert processed.authorizing_channel_user_id is None


def test_is_encrypted_requires_all_meta_flow_fields() -> None:
    assert is_encrypted(
        {
            "encrypted_flow_data": "data",
            "encrypted_aes_key": "key",
            "initial_vector": "iv",
        }
    )
    assert not is_encrypted({"encrypted_flow_data": "data"})


def test_settings_require_meta_app_secret_outside_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    required_env = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
        "REDIS_URL": "redis://redis:6379",
        "OPENAI_API_KEY": "openai-key",
        "MONO_API_KEY": "mono-key",
        "FLUTTERWAVE_SECRET_KEY": "flutterwave-key",
        "META_ACCESS_TOKEN": "meta-access-token",
        "META_VERIFY_TOKEN": "meta-verify-token",
        "META_PHONE_NUMBER_ID": "meta-phone-number-id",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_WEBHOOK_SECRET_TOKEN": "telegram-secret",
    }
    for key, value in required_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("META_APP_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="META_APP_SECRET"):
        Settings()
