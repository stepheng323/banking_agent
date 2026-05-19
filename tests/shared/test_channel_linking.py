from types import SimpleNamespace
from typing import Any

import pytest

from shared.services import channel_linking as channel_linking_module
from shared.services.auth import AuthorizationResult
from shared.services.channel_linking import build_channel_link_pin_token, complete_channel_link_with_pin


class _SessionManagerStub:
    def __init__(self, sessions: dict[str, dict[str, Any]] | None = None) -> None:
        self.sessions = sessions or {}
        self.deleted: list[str] = []

    async def get_session(self, flow_token: str) -> dict[str, Any]:
        return self.sessions.get(flow_token, {})

    async def delete_session(self, flow_token: str) -> None:
        self.deleted.append(flow_token)
        self.sessions.pop(flow_token, None)


class _AuthStub:
    def __init__(self, result: AuthorizationResult) -> None:
        self.result = result
        self.verify_calls: list[dict[str, str | None]] = []
        self.stored: list[tuple[str, AuthorizationResult]] = []

    async def verify_pin(
        self,
        phone_number: str,
        pin: str,
        idempotency_key: str,
        transaction_type: str | None = None,
    ) -> AuthorizationResult:
        self.verify_calls.append(
            {
                "phone_number": phone_number,
                "pin": pin,
                "idempotency_key": idempotency_key,
                "transaction_type": transaction_type,
            }
        )
        return self.result

    async def store_pin_verification_result(self, idempotency_key: str, result: AuthorizationResult) -> None:
        self.stored.append((idempotency_key, result))


def _pending_session() -> dict[str, Any]:
    return {
        "purpose": "channel_identity_link",
        "user_id": "user-1",
        "phone_number": "2348162511023",
        "requested_channel": "telegram",
        "requested_channel_user_id": "12345",
        "requested_channel_actor_id": "12345",
        "authorizing_channel": "whatsapp",
        "authorizing_channel_user_id": "2348162511023",
        "step": "pending_existing_channel_authorization",
    }


@pytest.mark.asyncio
async def test_channel_link_pin_completion_links_requested_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManagerStub({"channel-link-token": _pending_session()})
    auth = _AuthStub(AuthorizationResult(verified=True, user_id="user-1", transaction_type="channel_link"))
    user = SimpleNamespace(id="user-1", phone_number="2348162511023")
    linked: list[tuple[str, str, str]] = []

    class _UsersStub:
        async def get_by_id(self, user_id: str) -> Any:
            assert user_id == "user-1"
            return user

        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "12345"
            return None

        async def link_channel_identity(self, user_id: str, channel: str, identity: str) -> None:
            linked.append((user_id, channel, identity))

    class _UnitOfWorkStub:
        def __init__(self) -> None:
            self.users = _UsersStub()

        async def __aenter__(self) -> "_UnitOfWorkStub":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            del exc_type, exc, tb
            return False

    cached: list[tuple[str, str, Any]] = []

    async def _store_channel_identity_user(channel: str, channel_user_id: str, cached_user: Any) -> None:
        cached.append((channel, channel_user_id, cached_user))

    monkeypatch.setattr(channel_linking_module, "UnitOfWork", _UnitOfWorkStub)
    monkeypatch.setattr(channel_linking_module, "store_channel_identity_user", _store_channel_identity_user)

    result = await complete_channel_link_with_pin(
        flow_token=build_channel_link_pin_token("channel-link-token"),
        pin="1234",
        authorizing_channel="whatsapp",
        authorizing_channel_user_id="08162511023",
        session_manager=session_manager,
        authorization_service=auth,  # type: ignore[arg-type]
    )

    assert result.success is True
    assert linked == [("user-1", "telegram", "12345")]
    assert session_manager.deleted == ["channel-link-token"]
    assert cached == [("telegram", "12345", user)]
    assert auth.verify_calls == [
        {
            "phone_number": "2348162511023",
            "pin": "1234",
            "idempotency_key": "channel-link-token",
            "transaction_type": "channel_link",
        }
    ]


@pytest.mark.asyncio
async def test_channel_link_pin_completion_rejects_invalid_pin() -> None:
    session_manager = _SessionManagerStub({"channel-link-token": _pending_session()})
    auth = _AuthStub(
        AuthorizationResult(
            verified=False,
            transaction_type="channel_link",
            error="Invalid PIN. 2 attempt(s) remaining.",
            attempts_remaining=2,
        )
    )

    result = await complete_channel_link_with_pin(
        flow_token=build_channel_link_pin_token("channel-link-token"),
        pin="0000",
        authorizing_channel="whatsapp",
        authorizing_channel_user_id="2348162511023",
        session_manager=session_manager,
        authorization_service=auth,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.status == "invalid_pin"
    assert result.attempts_remaining == 2
    assert session_manager.deleted == []


@pytest.mark.asyncio
async def test_channel_link_pin_completion_rejects_pin_for_different_user() -> None:
    session_manager = _SessionManagerStub({"channel-link-token": _pending_session()})
    auth = _AuthStub(AuthorizationResult(verified=True, user_id="user-2", transaction_type="channel_link"))

    result = await complete_channel_link_with_pin(
        flow_token=build_channel_link_pin_token("channel-link-token"),
        pin="1234",
        authorizing_channel="whatsapp",
        authorizing_channel_user_id="2348162511023",
        session_manager=session_manager,
        authorization_service=auth,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.status == "wrong_authorizer"
    assert session_manager.deleted == []


@pytest.mark.asyncio
async def test_channel_link_pin_completion_rejects_stale_telegram_authorizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = {
        **_pending_session(),
        "requested_channel": "whatsapp",
        "requested_channel_user_id": "2348162511023",
        "authorizing_channel": "telegram",
        "authorizing_channel_user_id": "927331985",
    }
    session_manager = _SessionManagerStub({"channel-link-token": session})
    auth = _AuthStub(AuthorizationResult(verified=True, user_id="user-1", transaction_type="channel_link"))
    user = SimpleNamespace(id="user-1", phone_number="2348162511023")

    class _UsersStub:
        async def get_by_id(self, user_id: str) -> Any:
            assert user_id == "user-1"
            return user

        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "927331985"
            return None

        async def link_channel_identity(self, user_id: str, channel: str, identity: str) -> None:
            del user_id, channel, identity
            raise AssertionError("stale authorizer must not link requested identity")

    class _UnitOfWorkStub:
        def __init__(self) -> None:
            self.users = _UsersStub()

        async def __aenter__(self) -> "_UnitOfWorkStub":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            del exc_type, exc, tb
            return False

    monkeypatch.setattr(channel_linking_module, "UnitOfWork", _UnitOfWorkStub)

    result = await complete_channel_link_with_pin(
        flow_token=build_channel_link_pin_token("channel-link-token"),
        pin="1234",
        authorizing_channel="telegram",
        authorizing_channel_user_id="927331985",
        session_manager=session_manager,
        authorization_service=auth,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.status == "wrong_authorizer"
    assert session_manager.deleted == []


@pytest.mark.asyncio
async def test_channel_link_pin_completion_rejects_expired_session() -> None:
    session_manager = _SessionManagerStub()
    auth = _AuthStub(AuthorizationResult(verified=True, user_id="user-1", transaction_type="channel_link"))

    result = await complete_channel_link_with_pin(
        flow_token=build_channel_link_pin_token("missing-token"),
        pin="1234",
        authorizing_channel="whatsapp",
        session_manager=session_manager,
        authorization_service=auth,  # type: ignore[arg-type]
    )

    assert result.success is False
    assert result.status == "expired"
    assert auth.verify_calls == []
