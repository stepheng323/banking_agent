from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.graphs.account.worker import AccountWorker
from shared.cache.flow_session_manager import FlowSessionManager
from shared.config.settings import settings
from shared.services.onboarding.bvn_verification import BvnVerificationService


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


class _FailingSessionManager:
    async def update_session_strict(self, flow_token: str, updates: dict[str, Any], *, verify: bool = False) -> bool:
        del flow_token, updates, verify
        return False


@pytest.mark.asyncio
async def test_build_link_account_flow_uses_canonical_phone_and_persists_session(
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

    monkeypatch.setattr("apps.chat.src.agent.graphs.account.worker.secrets.token_urlsafe", lambda _: "opaque-link-token")

    flow = await worker._build_link_account_flow(
        {
            "phone_number": "telegram-chat-id",
            "profile": {"phone_number": "2348000000000", "extra_data": {"bvn": "12345678901"}},
            "language": "en",
            "channel": "telegram",
        }
    )

    assert flow["flow_config"]["flow_token"] == "link-opaque-link-token"
    assert "2348000000000" not in flow["flow_config"]["flow_token"]

    stored = await BvnVerificationService(session_manager).get_session_data("link-opaque-link-token")
    assert stored["phone_number"] == "2348000000000"
    assert stored["bvn"] == "12345678901"
    assert stored["session_id"] == "mono-session-123"
    assert stored["step"] == "method_selection"
    assert stored["is_account_linking"] is True
    assert stored["channel"] == "telegram"


@pytest.mark.asyncio
async def test_build_link_account_flow_returns_retryable_error_when_session_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.whatsapp, "account_linking_flow_id", "flow-link-123")
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_BankingProviderStub(),
        session_manager=_FailingSessionManager(),
        direct_debit_provider=None,
    )

    monkeypatch.setattr("apps.chat.src.agent.graphs.account.worker.secrets.token_urlsafe", lambda _: "opaque-link-token")

    flow = await worker._build_link_account_flow(
        {
            "phone_number": "2348000000000",
            "profile": {"phone_number": "2348000000000", "extra_data": {"bvn": "12345678901"}},
            "language": "en",
            "channel": "telegram",
        }
    )

    assert flow == {"error": "Failed to start linking."}
