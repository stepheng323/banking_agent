import hashlib
import hmac
import importlib
import json
from collections.abc import Mapping
from typing import Any

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from apps.gateway.adapters import meta_whatsapp
from apps.gateway.adapters.meta_whatsapp import WebhookSignatureError, verify_meta_signature
from apps.gateway.api.webhooks.whatsapp.flows import request_processor
from apps.gateway.api.webhooks.whatsapp.flows import session_owner as session_owner_module
from apps.gateway.api.webhooks.whatsapp.flows.request_processor import process_flow_request
from apps.gateway.api.webhooks.whatsapp.flows.router import flow_webhook
from apps.gateway.api.webhooks.whatsapp.message.router import whatsapp_webhook
from shared.config.settings import Settings
from shared.utils.flow_decryption import is_encrypted

flow_router_module = importlib.import_module("apps.gateway.api.webhooks.whatsapp.flows.router")


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
async def test_process_flow_request_captures_flow_action_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    processed, error = await process_flow_request(_json_request({"version": "3.0", "action": "ping"}))

    assert error is None
    assert processed is not None
    assert processed.screen is None
    assert processed.action == "ping"
    assert processed.version == "3.0"


@pytest.mark.asyncio
async def test_process_flow_request_falls_back_to_data_flow_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    processed, error = await process_flow_request(
        _json_request(
            {
                "version": "3.0",
                "action": "data_exchange",
                "screen": "Pin",
                "data": {"pin": "123456", "flow_token": "transfer-pin-idem-1-2348162511023"},
            }
        )
    )

    assert error is None
    assert processed is not None
    assert processed.flow_token == "transfer-pin-idem-1-2348162511023"


@pytest.mark.asyncio
async def test_flow_webhook_ping_returns_versioned_active_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    response = await flow_webhook(_json_request({"version": "3.0", "action": "ping"}))

    assert response.status_code == 200
    assert json.loads(response.body) == {"version": "3.0", "data": {"status": "active"}}


@pytest.mark.asyncio
async def test_flow_webhook_data_exchange_launch_returns_pin_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    response = await flow_webhook(
        _json_request(
            {
                "version": "3.0",
                "action": "data_exchange",
                "flow_token": "transfer-pin-idem-1-2348162511023",
            }
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"version": "3.0", "screen": "Pin", "data": {}}


@pytest.mark.asyncio
async def test_flow_webhook_init_with_blank_screen_returns_pin_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    response = await flow_webhook(
        _json_request(
            {
                "version": "3.0",
                "action": "INIT",
                "screen": "",
                "data": {},
                "flow_token": "transfer-pin-idem-1-2348162511023",
            }
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"version": "3.0", "screen": "Pin", "data": {}}


@pytest.mark.asyncio
async def test_flow_webhook_infers_pin_screen_for_screenless_pin_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    calls: list[dict[str, Any]] = []

    async def _handle_transaction_pin(*args: Any, **kwargs: Any) -> JSONResponse:
        calls.append({"args": args, "kwargs": kwargs})
        return JSONResponse({"ok": True})

    monkeypatch.setattr(flow_router_module, "handle_transaction_pin", _handle_transaction_pin)

    response = await flow_webhook(
        _json_request(
            {
                "version": "3.0",
                "action": "data_exchange",
                "data": {"pin": "123456", "flow_token": "transfer-pin-idem-1-2348162511023"},
            }
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["args"][0] == {"pin": "123456", "flow_token": "transfer-pin-idem-1-2348162511023"}
    assert calls[0]["args"][1] == "transfer-pin-idem-1-2348162511023"


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
                "data": {"pin": "123456", "wa_id": "0000000000"},
                "flow_token": "channel-link-pin-channel-link-token",
            }
        )
    )

    assert error is None
    assert processed is not None
    assert processed.authorizing_channel_user_id == "2348162511023"


@pytest.mark.asyncio
async def test_flow_webhook_channel_link_pin_passes_provider_whatsapp_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_processor.settings.runtime, "app_env", "development")
    monkeypatch.setattr(request_processor.settings.runtime, "infrastructure_environment", "production")

    calls: list[dict[str, Any]] = []

    async def _handle_channel_link_pin(*args: Any, **kwargs: Any) -> JSONResponse:
        calls.append({"args": args, "kwargs": kwargs})
        return JSONResponse({"ok": True})

    monkeypatch.setattr(flow_router_module, "handle_channel_link_pin", _handle_channel_link_pin)

    response = await flow_webhook(
        _json_request(
            {
                "version": "3.0",
                "screen": "Pin",
                "contacts": [{"wa_id": "2348162511023"}],
                "data": {"pin": "123456"},
                "flow_token": "channel-link-pin-channel-link-token",
            }
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["args"][:5] == (
        {"pin": "123456"},
        "channel-link-pin-channel-link-token",
        False,
        b"",
        b"",
    )
    assert calls[0]["kwargs"] == {"authorizing_channel_user_id": "2348162511023"}


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
                "data": {"pin": "123456", "wa_id": "2348162511023"},
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


@pytest.mark.asyncio
async def test_whatsapp_flow_session_owner_rejects_mismatched_provider_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SessionManagerStub:
        async def get_session(self, flow_token: str) -> dict[str, Any]:
            assert flow_token == "onboarding-opaque-token"
            return {
                "phone_number": "2348162511023",
                "channel": "whatsapp",
                "channel_user_id": "2348162511023",
            }

    monkeypatch.setattr(session_owner_module.settings.runtime, "app_env", "production")
    monkeypatch.setattr(session_owner_module, "session_manager", _SessionManagerStub())

    result = await session_owner_module.verify_whatsapp_flow_session_owner(
        flow_token="onboarding-opaque-token",
        authorizing_channel_user_id="2348000000000",
        screen="BVN_ENTRY",
    )

    assert result.ok is False
    assert result.reason == "owner_mismatch"


def test_settings_require_meta_app_secret_outside_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    required_env = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
        "REDIS_URL": "redis://redis:6379",
        "OPENAI_API_KEY": "openai-key",
        "MONO_API_KEY": "mono-key",
        "MONO_WEBHOOK_SECRET": "mono-webhook-secret",
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
