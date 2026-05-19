"""Tests for session-scoped repository wrappers."""

from typing import Any

import pytest

from shared.repositories.session_scoped import SessionScopedUserRepository
from shared.repositories.user_repository import UserRepository


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closes += 1


@pytest.mark.asyncio
async def test_session_scoped_user_repository_wraps_channel_identity_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_FakeSession] = []
    calls: list[tuple[Any, str, str]] = []

    def _session_factory() -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    async def _lookup(self: UserRepository, phone_number: str, channel: str) -> str:
        calls.append((self.db, phone_number, channel))
        return "927331985"

    monkeypatch.setattr(UserRepository, "get_channel_identity_by_phone", _lookup)

    repository = SessionScopedUserRepository(_session_factory)
    result = await repository.get_channel_identity_by_phone("2348162511023", "telegram")

    assert result == "927331985"
    assert not hasattr(repository, "db")
    assert calls == [(sessions[0], "2348162511023", "telegram")]
    assert sessions[0].commits == 1
    assert sessions[0].rollbacks == 0
    assert sessions[0].closes == 1
