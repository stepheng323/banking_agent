"""Telegram onboarding/relink router tests."""

from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.workers.account.linking import build_link_account_flow
from apps.chat.src.agent.workers.account.worker import AccountWorker
from apps.gateway.api.webhooks.telegram.onboarding import (
    AccountInput,
    LinkingAccountInput,
    LinkingMethodInput,
    LinkingOtpInput,
    telegram_linking_account,
    telegram_linking_method,
    telegram_linking_otp,
    telegram_onboarding_account,
)
from banking.accounts.onboarding.bvn_verification import BvnVerificationService
from shared.cache.flow_session_manager import FlowSessionManager, SessionReadResult
from shared.config.settings import settings

LINK_TOKEN = "link-opaque-token"
TELEGRAM_USER_DATA = {"user": '{"id": 12345}'}


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

    async def get_session_status(self, flow_token: str) -> SessionReadResult:
        del flow_token
        if self._session is None:
            return SessionReadResult(status="missing")
        return SessionReadResult(status="found", data=self._session)

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


class _RedisStub:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class _DummyRepo:
    async def get_by_user(self, _user_id: str) -> list[Any]:
        return []


class _DummyLLM:
    def with_structured_output(self, _schema: Any) -> Any:
        raise NotImplementedError


class _BankingProviderStub:
    async def initiate_bvn_lookup(self, _bvn: str) -> Any:
        return SimpleNamespace(
            success=True,
            verification_methods=[{"method": "sms", "hint": "0818***6496"}],
            session_id="mono-session-123",
            bvn="12345678901",
        )


@pytest.mark.asyncio
async def test_linking_method_bootstrap_returns_preseeded_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
            "channel": "telegram",
            "channel_user_id": "12345",
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)

    result = await telegram_linking_method(
        LinkingMethodInput(flow_token=LINK_TOKEN, method=None),
        user_data=TELEGRAM_USER_DATA,
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
            "channel": "telegram",
            "channel_user_id": "12345",
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)

    result = await telegram_linking_method(
        LinkingMethodInput(flow_token=LINK_TOKEN, method="sms"),
        user_data=TELEGRAM_USER_DATA,
    )

    assert result["success"] is True
    assert bvn_stub.send_otp_calls == [(LINK_TOKEN, "sms")]


@pytest.mark.asyncio
async def test_linking_otp_verifies_and_returns_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
            "channel": "telegram",
            "channel_user_id": "12345",
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)

    result = await telegram_linking_otp(
        LinkingOtpInput(flow_token=LINK_TOKEN, otp="123456"),
        user_data=TELEGRAM_USER_DATA,
    )

    assert result["success"] is True
    assert result["data"]["accounts"][0]["id"] == "acc_1"
    assert bvn_stub.verify_otp_calls == [(LINK_TOKEN, "123456")]


@pytest.mark.asyncio
async def test_linking_account_route_uses_account_add(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(
        {
            "is_account_linking": True,
            "bvn": "12345678901",
            "methods": [{"id": "sms", "title": "0818***6496"}],
            "channel": "telegram",
            "channel_user_id": "12345",
        }
    )
    add_stub = _AccountAddServiceStub()
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.account_add_service", add_stub)

    result = await telegram_linking_account(
        LinkingAccountInput(flow_token=LINK_TOKEN, account_id="acc_1"),
        user_data=TELEGRAM_USER_DATA,
    )

    assert result["success"] is True
    assert add_stub.calls == [(LINK_TOKEN, "acc_1")]


@pytest.mark.asyncio
async def test_linking_endpoints_reject_missing_or_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    bvn_stub = _BvnServiceStub(None)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)

    method_result = await telegram_linking_method(
        LinkingMethodInput(flow_token=LINK_TOKEN, method=None),
        user_data=TELEGRAM_USER_DATA,
    )
    otp_result = await telegram_linking_otp(
        LinkingOtpInput(flow_token=LINK_TOKEN, otp="123456"),
        user_data=TELEGRAM_USER_DATA,
    )

    assert method_result["success"] is False
    assert otp_result["success"] is False
    assert "expired" in method_result["error"].lower()
    assert "expired" in otp_result["error"].lower()


@pytest.mark.asyncio
async def test_onboarding_account_route_still_uses_onboarding_select(monkeypatch: pytest.MonkeyPatch) -> None:
    add_stub = _AccountAddServiceStub()
    account_stub = _AccountServiceStub()
    bvn_stub = _BvnServiceStub(
        {
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_user_id": "12345",
            "step": "account_selection",
        }
    )
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.account_add_service", add_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.account_service", account_stub)
    monkeypatch.setattr("apps.gateway.api.webhooks.telegram.onboarding.bvn_service", bvn_stub)

    result = await telegram_onboarding_account(
        AccountInput(flow_token="onboarding-opaque-token", account_id="acc_2"),
        user_data=TELEGRAM_USER_DATA,
    )

    assert result["success"] is True
    assert account_stub.calls == [("onboarding-opaque-token", "acc_2")]
    assert add_stub.calls == []


@pytest.mark.asyncio
async def test_account_worker_link_token_bootstraps_telegram_relink_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.whatsapp, "account_linking_flow_id", "flow-link-123")
    redis = _RedisStub()
    session_manager = FlowSessionManager(redis=redis, key_prefix="onboarding")
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_BankingProviderStub(),
        session_manager=session_manager,
        direct_debit_provider=None,
    )

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.account.linking.secrets.token_urlsafe", lambda _: "opaque-link-token"
    )
    monkeypatch.setattr(
        "apps.gateway.api.webhooks.telegram.onboarding.bvn_service",
        BvnVerificationService(session_manager),
    )

    flow = await build_link_account_flow(
        context={
            "phone_number": "telegram-chat-id",
            "profile": {"phone_number": "2348000000000", "extra_data": {"bvn": "12345678901"}},
            "language": "en",
            "channel": "telegram",
        },
        banking_provider=worker.banking_provider,
        session_manager=worker.session_manager,
    )

    result = await telegram_linking_method(
        LinkingMethodInput(flow_token=flow["flow_config"]["flow_token"], method=None),
        user_data={"user": {"id": "telegram-chat-id"}},
    )

    assert result["success"] is True
    assert result["data"]["bvn"] == "12345678901"
    assert result["data"]["methods"] == [{"id": "sms", "title": "0818***6496"}]
