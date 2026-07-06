import json
from typing import Any

import pytest

from apps.gateway.api.webhooks.whatsapp.flows import session_owner as session_owner_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers import bvn_handler as handler_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers.bvn_handler import handle_bvn_entry


@pytest.mark.asyncio
async def test_whatsapp_bvn_entry_rejects_missing_bvn() -> None:
    response = await handle_bvn_entry(
        "",
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "BVN_ENTRY"
    assert "BVN is required" in body["data"]["error_message"]


@pytest.mark.asyncio
async def test_whatsapp_bvn_entry_rejects_missing_flow_token() -> None:
    response = await handle_bvn_entry(
        "12345678901",
        "",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "BVN_ENTRY"
    assert "flow_token is required" in body["data"]["error_message"]


@pytest.mark.asyncio
async def test_whatsapp_bvn_entry_session_owner_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=False, reason="owner_mismatch")

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )

    response = await handle_bvn_entry(
        "12345678901",
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "BVN_ENTRY"
    assert "Invalid or expired session" in body["data"]["error_message"]


@pytest.mark.asyncio
async def test_whatsapp_bvn_entry_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=True, session={"phone_number": "2348162511023"})

    async def _fake_initiate_bvn(flow_token: str, bvn: str) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "bvn": bvn,
                "methods": ["otp"],
            },
        }

    async def _fake_get_by_phone(*args, **kwargs):
        return None

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )
    monkeypatch.setattr(handler_module.bvn_service, "initiate_bvn_verification", _fake_initiate_bvn)
    monkeypatch.setattr(
        "banking.identity.repositories.user_repository.UserRepository.get_by_phone",
        _fake_get_by_phone,
    )

    response = await handle_bvn_entry(
        "12345678901",
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "METHOD_SELECTION"
    assert body["data"]["bvn"] == "12345678901"
    assert body["data"]["methods"] == ["otp"]


@pytest.mark.asyncio
async def test_whatsapp_bvn_entry_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=True, session={"phone_number": "2348162511023"})

    async def _fake_initiate_bvn(flow_token: str, bvn: str) -> dict[str, Any]:
        return {
            "success": False,
            "error": "BVN verification failed",
        }

    async def _fake_get_by_phone(*args, **kwargs):
        return None

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )
    monkeypatch.setattr(handler_module.bvn_service, "initiate_bvn_verification", _fake_initiate_bvn)
    monkeypatch.setattr(
        "banking.identity.repositories.user_repository.UserRepository.get_by_phone",
        _fake_get_by_phone,
    )

    response = await handle_bvn_entry(
        "12345678901",
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "BVN_ENTRY"
    assert body["data"]["error_message"] == "BVN verification failed"
