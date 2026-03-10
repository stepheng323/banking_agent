"""Telegram onboarding/relink router tests."""

import pytest

from apps.gateway.api.webhooks.telegram.router import (
    AccountInput,
    LinkingSessionInput,
    telegram_onboarding_account,
    telegram_onboarding_linking_session,
)


class _BvnServiceStub:
    def __init__(self, session: dict | None) -> None:
        self._session = session

    async def get_session_data(self, flow_token: str) -> dict | None:
        del flow_token
        return self._session


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
async def test_linking_session_returns_preseeded_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.gateway.api.webhooks.telegram.router.bvn_service",
        _BvnServiceStub(
            {
                "is_account_linking": True,
                "bvn": "12345678901",
                "methods": [{"id": "sms", "title": "0818***6496"}],
            }
        ),
    )

    result = await telegram_onboarding_linking_session(
        LinkingSessionInput(flow_token="link-2348000000000-1700000000"),
        user_data={},
    )

    assert result["success"] is True
    assert result["data"]["bvn"] == "12345678901"
    assert result["data"]["methods"] == [{"id": "sms", "title": "0818***6496"}]


@pytest.mark.asyncio
async def test_linking_session_rejects_missing_or_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.bvn_service", _BvnServiceStub(None))

    result = await telegram_onboarding_linking_session(
        LinkingSessionInput(flow_token="link-2348000000000-1700000000"),
        user_data={},
    )

    assert result["success"] is False
    assert "expired" in result["error"].lower()


@pytest.mark.asyncio
async def test_account_route_uses_account_add_for_link_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    add_stub = _AccountAddServiceStub()
    account_stub = _AccountServiceStub()
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.account_add_service", add_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.router.account_service", account_stub)

    result = await telegram_onboarding_account(
        AccountInput(flow_token="link-2348000000000-1700000000", account_id="acc_1"),
        user_data={},
    )

    assert result["success"] is True
    assert add_stub.calls == [("link-2348000000000-1700000000", "acc_1")]
    assert account_stub.calls == []


@pytest.mark.asyncio
async def test_account_route_uses_onboarding_select_for_non_link_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
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
