from datetime import date
from typing import Any

import pytest

from banking.transactions.query.handlers.cash_flow import handle_cash_flow
from banking.transactions.query.models.domain import (
    Aggregation,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    TimeRange,
)


class _Provider:
    async def get_transactions(
        self,
        account_id: str,
        *,
        start_date: str,
        end_date: str,
        limit: int = 100,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        del start_date, end_date, limit, user_id, mock_account_slot
        if account_id == "access":
            return [
                {
                    "id": "a1",
                    "amount": 100000,
                    "type": "credit",
                    "date": "2026-06-10",
                    "status": "successful",
                    "bank_name": "Access Bank",
                    "source_account_id": "access",
                    "source_account_number": "6000000003",
                },
                {
                    "id": "a2",
                    "amount": 25000,
                    "type": "debit",
                    "date": "2026-06-11",
                    "status": "successful",
                    "bank_name": "Access Bank",
                    "source_account_id": "access",
                    "source_account_number": "6000000003",
                },
            ]
        return [
            {
                "id": "f1",
                "amount": 50000,
                "type": "debit",
                "date": "2026-06-12",
                "status": "successful",
                "bank_name": "First Bank",
                "source_account_id": "first",
                "source_account_number": "6000000001",
            }
        ]


@pytest.mark.asyncio
async def test_cash_flow_by_account_returns_account_breakdown() -> None:
    contract = QueryExecutionContract.from_query_ir(
        QueryIR(
            intent=QueryIntent.CASH_FLOW_SUMMARY,
            aggregation=Aggregation(type="breakdown", group_by="account"),
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 27)),
        )
    )

    result = await handle_cash_flow(
        _Provider(),  # type: ignore[arg-type]
        contract,
        account_id="access",
        account_ids=["access", "first"],
        language="en",
    )

    assert result.summary_text.startswith("Cash flow by account")
    assert "Access Bank · ···0003" in result.summary_text
    assert "₦100,000 came in · ₦25,000 went out · up ₦75,000" in result.summary_text
    assert "First Bank · ···0001" in result.summary_text
