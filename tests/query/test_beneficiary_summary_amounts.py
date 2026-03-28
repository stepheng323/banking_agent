from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.beneficiary import handle_beneficiary_summary
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Filters,
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
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
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


class _RecipientVariantProviderStub:
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
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
        return [
            {
                "id": "tx-1",
                "type": "debit",
                "amount": 5000,
                "recipient_name": "Gaines.",
                "narration": "TRANSFER TO RANDOM PAYEE",
                "date": "2026-03-09",
            },
            {
                "id": "tx-2",
                "type": "debit",
                "amount": 20000,
                "recipient_name": "Gaines",
                "narration": "PAYMENT TO SOMEONE ELSE",
                "date": "2026-03-08",
            },
            {
                "id": "tx-3",
                "type": "debit",
                "amount": 10000,
                "recipient_name": "Payment",
                "narration": "PAYMENT TO PAYMENT",
                "date": "2026-03-07",
            },
        ]


class _AmountScopedRecipientProviderStub:
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
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
        return [
            {
                "id": "tx-1",
                "type": "debit",
                "amount": 50000,
                "recipient_name": "Adesanya Kunle",
                "narration": "TRANSFER TO Adesanya Kunle",
                "date": "2026-03-27",
            },
            {
                "id": "tx-2",
                "type": "debit",
                "amount": 20000,
                "recipient_name": "Emmanuel Okoro",
                "narration": "TRANSFER TO Emmanuel Okoro",
                "date": "2026-03-26",
            },
        ]


@pytest.mark.asyncio
async def test_beneficiary_summary_merges_trivial_recipient_name_variants() -> None:
    query = NormalizedQuery(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 7), end=date(2026, 3, 10), granularity="day"),
        aggregation=Aggregation(type="sum", sort_by="count", limit=5),
    )
    contract = QueryExecutionContract.from_normalized_query(query)
    provider = _RecipientVariantProviderStub()

    result = await handle_beneficiary_summary(
        provider,
        contract,
        account_id="acc-1",
        account_ids=["acc-1"],
        language="en",
    )

    assert "Gaines • ₦25,000 (2x)" in result.summary_text
    assert "Gaines. • ₦" not in result.summary_text
    assert result.items
    assert result.items[0].description == "Gaines"
    assert result.items[0].metadata and result.items[0].metadata.get("recipient_name") == "Gaines"


@pytest.mark.asyncio
async def test_beneficiary_summary_header_mentions_lower_bound_amount_scope() -> None:
    query = NormalizedQuery(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 14), end=date(2026, 3, 27), granularity="day"),
        filters=Filters(transaction_type="debit", min_amount=20000),
        aggregation=Aggregation(type="sum", sort_by="count", limit=5),
    )
    contract = QueryExecutionContract.from_normalized_query(query)
    provider = _AmountScopedRecipientProviderStub()

    result = await handle_beneficiary_summary(
        provider,
        contract,
        account_id="acc-1",
        account_ids=["acc-1"],
        language="en",
    )

    assert "*Recipients I sent over ₦20,000 to* — Mar 14 – Mar 27" in result.summary_text


@pytest.mark.asyncio
async def test_beneficiary_summary_header_mentions_exact_amount_scope() -> None:
    query = NormalizedQuery(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 14), end=date(2026, 3, 27), granularity="day"),
        filters=Filters(transaction_type="debit", min_amount=20000, max_amount=20000),
        aggregation=Aggregation(type="sum", sort_by="count", limit=5),
    )
    contract = QueryExecutionContract.from_normalized_query(query)
    provider = _AmountScopedRecipientProviderStub()

    result = await handle_beneficiary_summary(
        provider,
        contract,
        account_id="acc-1",
        account_ids=["acc-1"],
        language="en",
    )

    assert "*Recipients I sent ₦20,000 to* — Mar 14 – Mar 27" in result.summary_text
