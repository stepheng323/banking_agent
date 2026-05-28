from datetime import date

import pytest

from apps.chat.src.agent.workers.query.handlers.affordability import handle_affordability
from apps.chat.src.agent.workers.query.models.domain import (
    QueryExecutionContract,
    QueryIntent,
)
from shared.clients.abstractions.banking import BalanceData


class FakeBalanceProvider:
    def __init__(self, balances: dict[str, float]) -> None:
        self.balances = balances

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceData | None:
        del real_time
        if account_id not in self.balances:
            return None
        return BalanceData(available_balance=self.balances[account_id], account_id=account_id)


def _contract(amount: float) -> QueryExecutionContract:
    return QueryExecutionContract(
        intent=QueryIntent.AFFORDABILITY,
        time_start=date(2026, 5, 1),
        time_end=date(2026, 5, 11),
        amount_check=amount,
        accounts_scope="all",
    )


@pytest.mark.asyncio
async def test_affordability_query_can_answer_single_covering_account() -> None:
    result = await handle_affordability(
        FakeBalanceProvider({"access": 50_000, "first": 10_000}),  # type: ignore[arg-type]
        _contract(35_000),
        "access",
        ["access", "first"],
        [
            {"account_id": "access", "bank_name": "Access Bank", "account_number": "6000000003", "mandate_status": "ready"},
            {"account_id": "first", "bank_name": "First Bank", "account_number": "6000000001", "mandate_status": "ready"},
        ],
        language="en",
    )

    assert "Access Bank" in result.summary_text
    assert "₦35,000" in result.summary_text
    assert "Balance after" in result.summary_text


@pytest.mark.asyncio
async def test_affordability_query_suggests_two_account_pool_when_needed() -> None:
    result = await handle_affordability(
        FakeBalanceProvider({"access": 30_000, "first": 10_000, "gtb": 3_000}),  # type: ignore[arg-type]
        _contract(35_000),
        "access",
        ["access", "first", "gtb"],
        [
            {"account_id": "access", "bank_name": "Access Bank", "account_number": "6000000003", "mandate_status": "ready"},
            {"account_id": "first", "bank_name": "First Bank", "account_number": "6000000001", "mandate_status": "ready"},
            {"account_id": "gtb", "bank_name": "GTBank", "account_number": "6000000002", "mandate_status": "ready"},
        ],
        language="en",
    )

    assert "pooling" in result.summary_text
    assert "Access Bank" in result.summary_text
    assert "First Bank" in result.summary_text
    assert "GTBank" not in result.summary_text


@pytest.mark.asyncio
async def test_affordability_query_ignores_non_ready_accounts() -> None:
    result = await handle_affordability(
        FakeBalanceProvider({"access": 30_000, "zenith": 100_000}),  # type: ignore[arg-type]
        _contract(50_000),
        "access",
        ["access", "zenith"],
        [
            {"account_id": "access", "bank_name": "Access Bank", "account_number": "6000000003", "mandate_status": "ready"},
            {"account_id": "zenith", "bank_name": "Zenith Bank", "account_number": "1234569384", "mandate_status": "pending"},
        ],
        language="en",
    )

    assert "cannot cover" in result.summary_text
    assert "₦30,000" in result.summary_text
    assert "Zenith" not in result.summary_text
