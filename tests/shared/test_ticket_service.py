from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from banking.support.services.ticket_service import TicketService
from shared.database.enums import SupportTicketStatusEnum


class _SessionStub:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _RepoStub:
    store: dict[str, SimpleNamespace] = {}
    created_count = 0

    def __init__(self, db: _SessionStub) -> None:
        self.db = db

    async def generate_ticket_code(self) -> str:
        self.__class__.created_count += 1
        return f"SUP-20260321-{self.__class__.created_count:04d}"

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        ticket = SimpleNamespace(**kwargs)
        self.__class__.store[ticket.ticket_code] = ticket
        return ticket

    async def get_by_ticket_code(self, ticket_code: str) -> SimpleNamespace | None:
        return self.__class__.store.get(ticket_code)

    async def get_open_tickets(self, user_id: str) -> list[SimpleNamespace]:
        return [ticket for ticket in self.__class__.store.values() if ticket.user_id == user_id]

    async def get_latest_open(self, user_id: str) -> SimpleNamespace | None:
        tickets = await self.get_open_tickets(user_id)
        return tickets[-1] if tickets else None

    async def update(self, instance: SimpleNamespace, **kwargs: Any) -> SimpleNamespace:
        for key, value in kwargs.items():
            setattr(instance, key, value)
        self.__class__.store[instance.ticket_code] = instance
        return instance


@pytest.fixture(autouse=True)
def _reset_repo_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _RepoStub.store = {}
    _RepoStub.created_count = 0
    monkeypatch.setattr("banking.support.services.ticket_service.SupportTicketRepository", _RepoStub)


@pytest.mark.asyncio
async def test_ticket_service_session_factory_creates_ticket_with_short_lived_session() -> None:
    session = _SessionStub()
    service = TicketService(session_factory=lambda: session)

    ticket = await service.create_ticket(
        user_id="user-1",
        intent="general_tx_issue",
        summary="Need help",
    )

    assert ticket.ticket_code == "SUP-20260321-0001"
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_ticket_service_session_factory_updates_ticket_status() -> None:
    create_session = _SessionStub()
    update_session = _SessionStub()
    sessions = iter([create_session, update_session])
    service = TicketService(session_factory=lambda: next(sessions))

    created = await service.create_ticket(
        user_id="user-1",
        intent="general_tx_issue",
        summary="Need help",
    )
    updated = await service.update_status(created.ticket_code, SupportTicketStatusEnum.RESOLVED)

    assert updated is not None
    assert updated.status == SupportTicketStatusEnum.RESOLVED.value
    assert getattr(updated, "resolved_at", None) is not None
    assert create_session.commit_calls == 1
    assert create_session.close_calls == 1
    assert update_session.commit_calls == 1
    assert update_session.close_calls == 1
