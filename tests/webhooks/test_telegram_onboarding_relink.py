"""Telegram onboarding/relink router tests."""

import pytest

from apps.gateway.api.webhooks.telegram.router import (
    AccountInput,
    LinkingAccountInput,
    LinkingMethodInput,
    LinkingOtpInput,
    telegram_linking_account,
    telegram_linking_method,
    telegram_linking_otp,
    telegram_onboarding_account,
)


class _BvnServiceStub:
    def __init__(
        self,
        session: dict | None,
        *,
        send_otp_result: dict | None = None,
        verify_otp_result: dict | None = None,
    ) -> None:
        self._session = session
        self.send_otp_result = send_otp_result or {"success": True, "data": {"bvn": "12345678901"}}
        self.verify_otp_result = verify_otp_result or {
            "success": True,
            "data": {
                "accounts": [{"id": "acc_1", "title": "Test Bank - 1234567890"}],
            },
        }
        self.send_otp_calls: list[tuple[str, str]] = []
        self.verify_otp_calls: list[tuple[str, str]] = []

    async def get_session_data(self, flow_token: str) -> dict | None:
        del flow_token
        return self._session

    async def send_otp(self, flow_token: str, method: str) -> dict:
        self.send_otp_calls.append((flow_token, method))
        return self.send_otp_result

    async def verify_otp(self, flow_token: str, otp: str) -> dict:
        self.verify_otp_calls.append((flow_token, otp))
        return self.verify_otp_result


class _AccountAddServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def add_account(self, flow_token: str, account_id: str) -> dict:
        self.calls.append((flow_token, account_id))
        return {"success": True, "data": {"mode": "relink"}}


class _AccountServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def select_account(self, flow_token: str, account_id: str) -> dict:
        self.calls.append((flow_token, account_id))
        return {"success": True, "data": {"mode": "onboarding"}}


@pytest.mark.asyncio
async def test_linking_method_bootstrap_returns_preseeded_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", bvn_stub)

    result = await telegram_linking_method(
        LinkingMethodInput(flow_token="link-2348000000000-1700000000", method=None),
        user_data={},
    )

    assert result["success"] is True
    assert result["data"]["bvn"] == "12345678901"
    assert result["data"]["methods"] == [{"id": "sms", "title": "0818***6496"}]
    assert bvn_stub.send_otp_calls == []


@pytest.mark.asyncio
async def test_linking_method_submit_sends_otp(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", bvn_stub)

    result = await telegram_linking_method(
        LinkingMethodInput(flow_token="link-2348000000000-1700000000", method="sms"),
        user_data={},
    )

    assert result["success"] is True
    assert bvn_stub.send_otp_calls == [("link-2348000000000-1700000000", "sms")]


@pytest.mark.asyncio
async def test_linking_otp_verifies_and_returns_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", bvn_stub)

    result = await telegram_linking_otp(
        LinkingOtpInput(flow_token="link-2348000000000-1700000000", otp="123456"),
        user_data={},
    )

    assert result["success"] is True
    assert result["data"]["accounts"][0]["id"] == "acc_1"
    assert bvn_stub.verify_otp_calls == [("link-2348000000000-1700000000", "123456")]


@pytest.mark.asyncio
async def test_linking_account_route_uses_account_add(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
        }
    )
    add_stub = _AccountAddServiceStub()
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", bvn_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.account_add_service", add_stub)

    result = await telegram_linking_account(
        LinkingAccountInput(flow_token="link-2348000000000-1700000000", account_id="acc_1"),
        user_data={},
    )

    assert result["success"] is True
    assert add_stub.calls == [("link-2348000000000-1700000000", "acc_1")]


@pytest.mark.asyncio
async def test_linking_endpoints_reject_missing_or_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(None)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", bvn_stub)

    method_result = await telegram_linking_method(
        LinkingMethodInput(flow_token="link-2348000000000-1700000000", method=None),
        user_data={},
    )
    otp_result = await telegram_linking_otp(
        LinkingOtpInput(flow_token="link-2348000000000-1700000000", otp="123456"),
        user_data={},
    )

    assert method_result["success"] is False
    assert otp_result["success"] is False
    assert "expired" in method_result["error"].lower()
    assert "expired" in otp_result["error"].lower()


@pytest.mark.asyncio
async def test_onboarding_account_route_still_uses_onboarding_select(monkeypatch: pytest.MonkeyPatch) -> None:
    add_stub = _AccountAddServiceStub()
    account_stub = _AccountServiceStub()
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.account_add_service", add_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.account_service", account_stub)

    result = await telegram_onboarding_account(
        AccountInput(flow_token="onboarding-12345", account_id="acc_2"),
        user_data={},
    )

    assert result["success"] is True
    assert account_stub.calls == [("onboarding-12345", "acc_2")]
    assert add_stub.calls == []
