from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.beneficiary import handle_beneficiary_summary
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    NormalizedQuery,
    QueryExecutionContract,
    QueryIntent,
    TimeRange,
)


class _ProviderStub:
    async def get_transactions(
        self,
        account_id: str,
        *,
        start_date: str,
        end_date: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit
        return [
            {
                "id": "tx-1",
                "type": "debit",
                "amount": 10000,
                "narration": "TRANSFER TO Mum",
                "date": "2026-03-09",
            },
            {
                "id": "tx-2",
                "type": "debit",
                "amount": 5000,
                "narration": "PAYMENT TO Mum",
                "date": "2026-03-08",
            },
        ]


@pytest.mark.asyncio
async def test_beneficiary_summary_uses_full_currency_amount_without_dividing_by_100() -> None:
    query = NormalizedQuery(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 10), granularity="day"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=5),
    )
    contract = QueryExecutionContract.from_normalized_query(query)
    provider = _ProviderStub()

    result = await handle_beneficiary_summary(
        provider,
        contract,
        account_id="acc-1",
        account_ids=["acc-1"],
        language="en",
    )

    assert "Mum • ₦15,000 (2x)" in result.summary_text
    assert result.items
    assert result.items[0].amount == 15000
