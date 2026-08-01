from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from banking.transactions.query.models.domain import (
    QueryIntent,
    QueryRequest,
    TimeRange,
)
from banking.transactions.query.models.operations import AffordabilitySpec, AssessOperation, Money
from banking.transactions.query.services.answers.coverage import build_query_coverage_answer
from tests.query.factories import make_query_request


class _CoverageRepo:
    covered_by_linked_id: dict[str, bool] = {}
    error: Exception | None = None

    async def is_window_covered(
        self,
        linked_account_id: str,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> bool:
        del start_date, end_date, provider, coverage_type
        if self.error is not None:
            raise self.error
        return self.covered_by_linked_id.get(str(linked_account_id), False)


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.bank_transaction_coverages = _CoverageRepo()

    async def __aenter__(self) -> _FakeUnitOfWork:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb


def _contract() -> QueryRequest:
    return make_query_request(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 4, 10), end=date(2026, 5, 10)),
    )


@pytest.fixture(autouse=True)
def _reset_coverage_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    _CoverageRepo.covered_by_linked_id = {}
    _CoverageRepo.error = None
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", _FakeUnitOfWork)


@pytest.mark.asyncio
async def test_coverage_answer_confirms_all_accounts_when_windows_are_covered() -> None:
    _CoverageRepo.covered_by_linked_id = {"linked_1": True, "linked_2": True}

    answer = await build_query_coverage_answer(
        accounts_info=[
            {"id": "linked_1", "account_id": "acc_1", "bank_name": "First Bank", "account_number": "6000000001"},
            {"id": "linked_2", "account_id": "acc_2", "bank_name": "GTBank", "account_number": "6000000002"},
        ],
        query_request=_contract(),
        session={},
        target_text=None,
    )

    assert "confirmed transaction coverage" in answer
    assert "First Bank (···0001)" in answer
    assert "GTBank (···0002)" in answer


@pytest.mark.asyncio
async def test_coverage_answer_explains_pending_mandate_for_target_account() -> None:
    answer = await build_query_coverage_answer(
        accounts_info=[
            {
                "id": "linked_1",
                "account_id": "acc_1",
                "bank_name": "Zenith Bank",
                "account_number": "6000009384",
                "mandate_status": "pending",
            }
        ],
        query_request=_contract(),
        session={},
        target_text="Zenith",
    )

    assert "authorization is pending" in answer
    assert "Complete the account authorization" in answer


@pytest.mark.asyncio
async def test_coverage_followup_after_affordability_can_explain_pending_account() -> None:
    """A coverage question must not assume every active query has a period."""
    affordability_contract = QueryRequest(
        operation=AssessOperation(
            assessment=AffordabilitySpec(amount=Money(amount=35000)),
        )
    )

    answer = await build_query_coverage_answer(
        accounts_info=[
            {
                "id": "linked_zenith",
                "account_id": "acc_zenith",
                "bank_name": "Zenith Bank",
                "account_number": "6000009384",
                "mandate_status": "pending",
            }
        ],
        query_request=affordability_contract,
        session={},
        target_text="Zenith",
    )

    assert "Zenith Bank" in answer
    assert "authorization is pending" in answer


@pytest.mark.asyncio
async def test_coverage_answer_is_honest_when_schema_is_unavailable() -> None:
    _CoverageRepo.error = RuntimeError('relation "bank_transaction_coverage" does not exist')

    answer = await build_query_coverage_answer(
        accounts_info=[
            {"id": "linked_1", "account_id": "acc_1", "bank_name": "Access Bank", "account_number": "6000000003"}
        ],
        query_request=_contract(),
        session={},
        target_text="Access",
    )

    assert "cannot confirm full coverage right now" in answer


@pytest.mark.asyncio
async def test_coverage_answer_handles_linked_account_without_local_transactions() -> None:
    _CoverageRepo.covered_by_linked_id = {"linked_1": False}

    answer = await build_query_coverage_answer(
        accounts_info=[
            {"id": "linked_1", "account_id": "acc_1", "bank_name": "First Bank", "account_number": "6000000001"}
        ],
        query_request=_contract(),
        session={"cached_transactions": []},
        target_text=None,
    )

    assert "best local view" in answer
    assert "coverage is incomplete" in answer
