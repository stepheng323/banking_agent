from __future__ import annotations

from typing import Any

import pytest

from shared.i18n.personality import PersonalityContext
from shared.transaction_runtime.personality_enrichment import enrich_transfer_personality_context


class _StatsRepo:
    def __init__(self, stats: dict[str, Any]):
        self.stats = stats
        self.calls: list[dict[str, Any]] = []

    async def get_successful_transfer_personality_stats(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"user_id": user_id, **kwargs})
        return self.stats


class _PooledRepo:
    def __init__(self, has_prior: bool):
        self.has_prior = has_prior

    async def has_prior_completed_pooled_transfer(self, user_id: str, **kwargs: Any) -> bool:
        del user_id, kwargs
        return self.has_prior


class _FailingRepo:
    async def get_successful_transfer_personality_stats(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        del user_id, kwargs
        raise RuntimeError("stats unavailable")


@pytest.mark.asyncio
async def test_enrichment_marks_first_largest_and_excludes_current_transaction() -> None:
    repo = _StatsRepo(
        {
            "prior_successful_transfer_count": 0,
            "prior_max_successful_transfer_amount": 0,
            "recipient_success_count_90d": 0,
        }
    )

    context = await enrich_transfer_personality_context(
        PersonalityContext(moment="success", amount=5000),
        user_id="user-1",
        transaction_repo=repo,
        payload={"recipient": {"account_number": "1234567890", "name": "Tolu"}},
        transaction_id="tx-1",
        idempotency_key="idem-1",
    )

    assert context.first_successful_transfer is True
    assert context.largest_successful_transfer is True
    assert repo.calls[0]["exclude_transaction_id"] == "tx-1"
    assert repo.calls[0]["exclude_idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_enrichment_marks_largest_transfer_against_prior_max() -> None:
    repo = _StatsRepo(
        {
            "prior_successful_transfer_count": 4,
            "prior_max_successful_transfer_amount": 4500,
            "recipient_success_count_90d": 0,
        }
    )

    context = await enrich_transfer_personality_context(
        PersonalityContext(moment="success", amount=5000),
        user_id="user-1",
        transaction_repo=repo,
        payload={"recipient": {"account_number": "1234567890", "name": "Tolu"}},
    )

    assert context.first_successful_transfer is False
    assert context.largest_successful_transfer is True


@pytest.mark.asyncio
async def test_enrichment_marks_frequent_recipient_from_recent_success_count() -> None:
    repo = _StatsRepo(
        {
            "prior_successful_transfer_count": 4,
            "prior_max_successful_transfer_amount": 7000,
            "recipient_success_count_90d": 2,
        }
    )

    context = await enrich_transfer_personality_context(
        PersonalityContext(moment="confirmation", amount=5000),
        user_id="user-1",
        transaction_repo=repo,
        payload={"recipient_account": "1234567890", "recipient_name": "Tolu"},
    )

    assert context.frequent_recipient is True
    assert context.recipient_success_count_90d == 2


@pytest.mark.asyncio
async def test_enrichment_marks_first_pooled_success_when_no_prior_completed_pooled_transfer() -> None:
    repo = _StatsRepo(
        {
            "prior_successful_transfer_count": 3,
            "prior_max_successful_transfer_amount": 7000,
            "recipient_success_count_90d": 0,
        }
    )

    context = await enrich_transfer_personality_context(
        PersonalityContext(moment="success", amount=5000, pooled_funding=True),
        user_id="user-1",
        transaction_repo=repo,
        funded_transfer_repo=_PooledRepo(has_prior=False),
        payload={"recipient": {"account_number": "1234567890", "name": "Tolu"}},
    )

    assert context.first_pooled_success is True


@pytest.mark.asyncio
async def test_enrichment_failure_falls_back_to_base_context() -> None:
    base = PersonalityContext(moment="success", amount=5000, saved_recipient=True)

    context = await enrich_transfer_personality_context(
        base,
        user_id="user-1",
        transaction_repo=_FailingRepo(),
        payload={"recipient": {"account_number": "1234567890", "name": "Tolu"}},
    )

    assert context == base
