import json
from typing import Any

import pytest

from apps.gateway.api.webhooks.whatsapp.flows import session_owner as session_owner_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers import account_selection_handler as handler_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers.account_selection_handler import (
    AccountSelectionInput,
    handle_account_selection,
)


@pytest.mark.asyncio
async def test_whatsapp_account_selection_rejects_missing_flow_token() -> None:
    response = await handle_account_selection(
        AccountSelectionInput(selected_account="acc_1"),
        "",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "ACCOUNT_SELECTION"
    assert "flow_token is required" in body["data"]["error_message"]


@pytest.mark.asyncio
async def test_whatsapp_account_selection_session_owner_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=False, reason="owner_mismatch")

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )

    response = await handle_account_selection(
        AccountSelectionInput(selected_account="acc_1"),
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "ACCOUNT_SELECTION"
    assert "Invalid or expired session" in body["data"]["error_message"]


@pytest.mark.asyncio
async def test_whatsapp_account_selection_onboarding_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=True, session={"is_account_linking": False})

    async def _fake_select_account(flow_token: str, account: str) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "bvn": "12345678901",
            },
        }

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )
    monkeypatch.setattr(handler_module.account_service, "select_account", _fake_select_account)

    response = await handle_account_selection(
        AccountSelectionInput(selected_account="acc_1"),
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "PIN_ENTRY"
    assert body["data"]["bvn"] == "12345678901"


@pytest.mark.asyncio
async def test_whatsapp_account_selection_linking_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify_owner(**kwargs: Any) -> session_owner_module.FlowOwnerCheck:
        return session_owner_module.FlowOwnerCheck(ok=True, session={"is_account_linking": True})

    async def _fake_add_account(flow_token: str, account: str) -> dict[str, Any]:
        return {
            "success": True,
            "data": {},
        }

    monkeypatch.setattr(
        handler_module,
        "verify_whatsapp_flow_session_owner",
        _fake_verify_owner,
    )
    monkeypatch.setattr(handler_module.account_add_service, "add_account", _fake_add_account)

    response = await handle_account_selection(
        AccountSelectionInput(selected_account="acc_1"),
        "flow-token-123",
        False,
        b"",
        b"",
    )
    body = json.loads(response.body)
    assert body["screen"] == "SUCCESS"
    assert body["data"]["extension_message_response"]["params"]["success"] is True
