from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from banking.risk.service import RiskDecisionService
from shared.config.settings import settings


class _FakeRiskDecisions:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def record(self, **kwargs):
        self.records.append(kwargs)
        return SimpleNamespace(**kwargs)


class _FakeBeneficiaries:
    async def get_transfer_by_account(self, user_id: str, account_number: str, bank_code: str | None):
        del user_id, account_number, bank_code
        return None


class _FakeUsers:
    def __init__(self, created_at):
        self.created_at = created_at

    async def get_channel_identity_record(self, channel: str, channel_user_id: str):
        del channel, channel_user_id
        return SimpleNamespace(created_at=self.created_at)


class _FakeTransactions:
    def __init__(self, transfers: list[SimpleNamespace] | None = None):
        self.transfers = transfers or []

    async def get_transfers_since(self, user_id: str, since, statuses=None, limit: int = 500):
        del user_id, statuses, limit
        return [tx for tx in self.transfers if tx.created_at >= since]


class _FakeFundedTransfers:
    def __init__(self, has_prior_completed: bool) -> None:
        self.has_prior_completed = has_prior_completed

    async def has_prior_completed_pooled_transfer(
        self,
        user_id: str,
        *,
        exclude_idempotency_key: str | None = None,
    ) -> bool:
        del user_id, exclude_idempotency_key
        return self.has_prior_completed


def _uow(*, channel_created_at, has_prior_completed=False, transfers=None) -> SimpleNamespace:
    return SimpleNamespace(
        risk_decisions=_FakeRiskDecisions(),
        beneficiaries=_FakeBeneficiaries(),
        users=_FakeUsers(channel_created_at),
        transactions=_FakeTransactions(transfers),
        funded_transfers=_FakeFundedTransfers(has_prior_completed),
    )


@pytest.mark.asyncio
async def test_high_risk_pooled_transfer_is_held_before_debit(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr(settings, "transfer_risk_enabled", True)
    monkeypatch.setattr(settings, "new_beneficiary_limit_ngn", 10_000)
    monkeypatch.setattr(settings, "new_beneficiary_cooling_seconds", 86_400)
    monkeypatch.setattr(settings, "new_channel_cooling_seconds", 86_400)
    monkeypatch.setattr(settings, "first_pooled_transfer_limit_ngn", 20_000)
    monkeypatch.setattr(settings, "manual_review_amount_ngn", 1_000_000)
    monkeypatch.setattr(settings, "transfer_hourly_amount_limit_ngn", 1_000_000)
    monkeypatch.setattr(settings, "transfer_daily_amount_limit_ngn", 5_000_000)
    monkeypatch.setattr(settings, "transfer_hourly_count_limit", 20)
    uow = _uow(channel_created_at=now - timedelta(minutes=5), has_prior_completed=False)
    payload = SimpleNamespace(
        idempotency_key="idem-1",
        amount=30_000,
        is_self=False,
        beneficiary_id=None,
        resolved_from_saved_beneficiary=False,
        recipient_account="1234567890",
        recipient_bank_code="000014",
        funding_plan={"is_single_source": False, "steps": [{"account_id": "account-1", "amount": 30_000}]},
    )
    context = SimpleNamespace(channel="whatsapp", channel_identity="2348000000000")
    worker_context = SimpleNamespace(user_id="user-1")

    result = await RiskDecisionService().evaluate_transfer(
        uow=uow,
        payload=payload,
        context=context,
        worker_context=worker_context,
    )

    assert result.decision == "hold_review"
    assert set(result.reason_codes) == {
        "first_high_value_pooled_transfer",
        "new_channel_identity",
        "new_or_unsaved_beneficiary",
    }
    assert uow.risk_decisions.records[0]["decision"] == "hold_review"


@pytest.mark.asyncio
async def test_low_risk_transfer_is_allowed(monkeypatch) -> None:
    old_channel = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    monkeypatch.setattr(settings, "transfer_risk_enabled", True)
    monkeypatch.setattr(settings, "new_beneficiary_limit_ngn", 10_000)
    monkeypatch.setattr(settings, "new_channel_cooling_seconds", 86_400)
    monkeypatch.setattr(settings, "first_pooled_transfer_limit_ngn", 20_000)
    monkeypatch.setattr(settings, "manual_review_amount_ngn", 1_000_000)
    monkeypatch.setattr(settings, "transfer_hourly_amount_limit_ngn", 1_000_000)
    monkeypatch.setattr(settings, "transfer_daily_amount_limit_ngn", 5_000_000)
    monkeypatch.setattr(settings, "transfer_hourly_count_limit", 20)
    uow = _uow(channel_created_at=old_channel, has_prior_completed=True)
    payload = SimpleNamespace(
        idempotency_key="idem-2",
        amount=5_000,
        is_self=True,
        beneficiary_id=None,
        resolved_from_saved_beneficiary=False,
        recipient_account="1234567890",
        recipient_bank_code="000014",
        funding_plan={"is_single_source": True, "steps": [{"account_id": "account-1", "amount": 5_000}]},
    )
    context = SimpleNamespace(channel="telegram", channel_identity="tg-1")
    worker_context = SimpleNamespace(user_id="user-1")

    result = await RiskDecisionService().evaluate_transfer(
        uow=uow,
        payload=payload,
        context=context,
        worker_context=worker_context,
    )

    assert result.decision == "allow"
    assert result.reason_codes == []
    assert uow.risk_decisions.records[0]["decision"] == "allow"
