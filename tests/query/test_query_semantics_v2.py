from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from banking.transactions.query.compiler.v2 import compile_query_request
from banking.transactions.query.executor import QueryExecutor
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    QueryExtractionResult,
    QueryFilters,
    QueryTimeRange,
    TimeReference,
)
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    ApproximateAmount,
    ExactAmount,
    ExplicitBaseline,
    Money,
    NamedAccount,
    QueryRequest,
    ResolvedPeriod,
    RetrieveOperation,
    SummarizeOperation,
    UnspecifiedCounterparty,
    VarianceDriversSpec,
)
from banking.transactions.query.services.fetching.semantic_filters import apply_query_scope
from banking.transactions.query.services.parsing.parser import QueryParser
from shared.clients.abstractions.banking import TransactionData


def _parser() -> QueryParser:
    return QueryParser(object())


def test_paid_someone_exact_amount_preserves_unspecified_recipient_semantics() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient="someone", min_amount=50_000, max_amount=50_000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="I paid someone 50k last week, please find it",
    )

    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))

    assert request.schema_version == 2
    assert isinstance(request.operation, RetrieveOperation)
    assert request.operation.scope.period == ResolvedPeriod(
        start=date(2026, 7, 13),
        end=date(2026, 7, 19),
        granularity="week",
    )
    predicate = request.operation.scope.predicate
    assert predicate.direction == "debit"
    assert predicate.event_types == ["transfer", "payment"]
    assert isinstance(predicate.amount, ExactAmount)
    assert predicate.amount.value.amount == Decimal("50000")
    assert predicate.counterparty is not None
    assert predicate.counterparty.role == "recipient"
    assert isinstance(predicate.counterparty.reference, UnspecifiedCounterparty)
    assert predicate.counterparty.reference.entity_type == "person"


def test_approximate_amount_uses_disclosed_ten_percent_tolerance() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(min_amount=50_000, max_amount=50_000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="Find the payment of about 50k last week",
    )

    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))

    assert isinstance(request.operation, RetrieveOperation)
    amount = request.operation.scope.predicate.amount
    assert isinstance(amount, ApproximateAmount)
    assert amount.minimum == Decimal("45000")
    assert amount.maximum == Decimal("55000")


def test_explicit_bank_filter_compiles_to_named_account_scope() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(bank="GTBank"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Show my GTBank transactions this month",
    )

    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))

    assert isinstance(request.operation, RetrieveOperation)
    assert isinstance(request.operation.scope.accounts, NamedAccount)
    assert request.operation.scope.accounts.name == "GTBank"


def test_directional_total_repairs_misclassified_fact_to_analytics() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        filters=QueryFilters(category="food"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        request_shape="fact",
        raw_query="How much did I spend on food this month?",
    )

    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))

    assert isinstance(request.operation, SummarizeOperation)
    assert request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert request.filters is not None
    assert request.filters.transaction_type == "debit"
    assert request.filters.category == ["food"]


def test_explicit_period_requires_valid_bounds() -> None:
    with pytest.raises(ValidationError):
        ExplicitBaseline(
            period=ResolvedPeriod(
                start=date(2026, 7, 20),
                end=date(2026, 7, 1),
            )
        )


def test_v2_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(
            {
                "schema_version": 2,
                "timezone": "Africa/Lagos",
                "operation": {
                    "kind": "assess",
                    "assessment": {
                        "type": "affordability",
                        "amount": Money(amount=Decimal("50000")).model_dump(),
                        "accounts": {"type": "all"},
                    },
                },
                "intent": "affordability",
            }
        )


def test_only_registered_variance_analysis_compiles() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.INSIGHT,
        insight={"type": "variance_drivers"},
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Why did I spend more this month?",
    )

    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))

    assert isinstance(request.operation, AnalyzeOperation)
    assert request.operation.analysis.type == "variance_drivers"


def test_unimplemented_analysis_is_absent_from_runtime_schema() -> None:
    with pytest.raises(ValidationError):
        VarianceDriversSpec.model_validate({"type": "anomalies"})


def test_unspecified_recipient_filters_out_non_payment_debits() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient="someone", min_amount=50_000, max_amount=50_000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="I paid someone 50k last week, please find it",
    )
    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))
    assert isinstance(request.operation, RetrieveOperation)

    matches = apply_query_scope(
        [
            {
                "id": "payment",
                "date": "2026-07-15",
                "amount": 50_000,
                "type": "debit",
                "narration": "NIP transfer to Tolu",
                "counterparty": "Tolu",
            },
            {
                "id": "fee",
                "date": "2026-07-15",
                "amount": 50_000,
                "type": "debit",
                "narration": "ATM cash withdrawal",
                "counterparty": "ATM",
            },
            {
                "id": "wrong-amount",
                "date": "2026-07-15",
                "amount": 55_000,
                "type": "debit",
                "narration": "NIP transfer to Ada",
                "counterparty": "Ada",
            },
        ],
        request.operation.scope,
    )

    assert [item["id"] for item in matches] == ["payment"]


def test_approximate_amount_filter_uses_inclusive_tolerance_bounds() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(min_amount=50_000, max_amount=50_000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="Find payments around 50k last week",
    )
    request = compile_query_request(_parser(), extraction, today=date(2026, 7, 24))
    assert isinstance(request.operation, RetrieveOperation)

    matches = apply_query_scope(
        [
            {"id": "low", "date": "2026-07-15", "amount": 45_000, "type": "debit", "narration": "payment"},
            {"id": "mid", "date": "2026-07-15", "amount": 50_000, "type": "debit", "narration": "payment"},
            {"id": "high", "date": "2026-07-15", "amount": 55_000, "type": "debit", "narration": "payment"},
            {"id": "outside", "date": "2026-07-15", "amount": 55_001, "type": "debit", "narration": "payment"},
        ],
        request.operation.scope,
    )

    assert [item["id"] for item in matches] == ["low", "mid", "high"]


class _SemanticRetrieveProvider:
    async def get_transactions(self, _account_id: str, **_kwargs: object) -> list[TransactionData]:
        return [
            TransactionData(
                transaction_id="payment",
                date="2026-07-15",
                narration="NIP transfer to Tolu",
                amount=50_000,
                transaction_type="debit",
                counterparty="Tolu",
            ),
            TransactionData(
                transaction_id="withdrawal",
                date="2026-07-15",
                narration="ATM cash withdrawal",
                amount=50_000,
                transaction_type="debit",
                counterparty="ATM",
            ),
        ]


@pytest.mark.asyncio
async def test_active_executor_uses_v2_semantics_for_unspecified_recipient() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient="someone", min_amount=50_000, max_amount=50_000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="I paid someone 50k last week, please find it",
    )
    parser = _parser()
    request = compile_query_request(parser, extraction, today=date(2026, 7, 24))

    result = await QueryExecutor(_SemanticRetrieveProvider()).execute(
        query=request,
        account_id="gtb",
        account_ids=["gtb"],
    )

    assert result.query_request == request
    assert result.items is not None
    assert [item.id for item in result.items] == ["payment"]
