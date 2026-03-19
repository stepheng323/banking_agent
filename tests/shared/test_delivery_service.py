from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from apps.core.src.agent.orchestrator.models.intents import Say
from apps.core.src.messaging.presenters.base import PresentationResult
from shared.clients.abstractions.messaging import MessagingClient
from shared.services.delivery_service import DeliveryAttemptResult, DeliveryService


class _RedisStub:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, ttl_seconds: int) -> None:
        del key, ttl_seconds

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)


class _PresenterStub:
    def __init__(self, *, message_ids: list[str] | None = None) -> None:
        self.message_ids = message_ids or ["msg-1"]
        self.calls: list[dict[str, Any]] = []

    async def present(self, intents: list[Any], context: Any) -> PresentationResult:
        self.calls.append({"intents": intents, "context": context})
        return PresentationResult(success=True, message_ids=self.message_ids)


@pytest.mark.asyncio
async def test_delivery_service_returns_delivered_status(monkeypatch: pytest.MonkeyPatch) -> None:
    presenter = _PresenterStub(message_ids=["tg-msg-1"])
    service = DeliveryService(messaging_clients={"telegram": cast(MessagingClient, SimpleNamespace(supports_flows=True))})
    service.redis = _RedisStub()

    monkeypatch.setattr("shared.services.delivery_service.PresenterFactory.create", lambda channel, client: presenter)

    result = await service.deliver_text(
        phone_number="2348000000000",
        channel="telegram",
        text="Still working",
        metadata={"progress_stage": "query.fetching_transactions"},
        dedupe_key="telegram:turn-1:progress:0:query.fetching_transactions",
    )

    assert result == DeliveryAttemptResult(status="delivered", message_ids=("tg-msg-1",))
    assert len(presenter.calls) == 1


@pytest.mark.asyncio
async def test_delivery_service_returns_deduped_completed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    presenter = _PresenterStub()
    service = DeliveryService(messaging_clients={"telegram": cast(MessagingClient, SimpleNamespace(supports_flows=True))})
    redis = _RedisStub()
    service.redis = redis

    monkeypatch.setattr("shared.services.delivery_service.PresenterFactory.create", lambda channel, client: presenter)

    intents = [Say(text="Still working")]
    ledger_key, _ = service._build_ledger_key(
        phone_number="2348000000000",
        channel="telegram",
        intents=intents,
        dedupe_key="telegram:turn-1:progress:0:query.fetching_transactions",
    )
    assert ledger_key is not None
    redis.hashes[ledger_key] = {"status": "completed"}

    result = await service.deliver_intents(
        phone_number="2348000000000",
        channel="telegram",
        intents=intents,
        metadata={
            "progress_stage": "query.fetching_transactions",
            "progress_turn_id": "turn-1",
            "dedupe_key": "telegram:turn-1:progress:0:query.fetching_transactions",
        },
        dedupe_key="telegram:turn-1:progress:0:query.fetching_transactions",
    )

    assert result == DeliveryAttemptResult(status="deduped_completed")
    assert presenter.calls == []


@pytest.mark.asyncio
async def test_delivery_service_returns_deduped_resumed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    presenter = _PresenterStub()
    service = DeliveryService(messaging_clients={"telegram": cast(MessagingClient, SimpleNamespace(supports_flows=True))})
    redis = _RedisStub()
    service.redis = redis

    monkeypatch.setattr("shared.services.delivery_service.PresenterFactory.create", lambda channel, client: presenter)

    intents = [Say(text="Still working")]
    ledger_key, _ = service._build_ledger_key(
        phone_number="2348000000000",
        channel="telegram",
        intents=intents,
        dedupe_key="telegram:turn-1:progress:0:query.fetching_transactions",
    )
    assert ledger_key is not None
    redis.hashes[ledger_key] = {"status": "sent", "message_ids": json.dumps(["tg-msg-2"])}

    result = await service.deliver_intents(
        phone_number="2348000000000",
        channel="telegram",
        intents=intents,
        metadata={
            "progress_stage": "query.fetching_transactions",
            "progress_turn_id": "turn-1",
            "dedupe_key": "telegram:turn-1:progress:0:query.fetching_transactions",
        },
        dedupe_key="telegram:turn-1:progress:0:query.fetching_transactions",
    )

    assert result == DeliveryAttemptResult(status="deduped_resumed", message_ids=("tg-msg-2",))
    assert presenter.calls == []
