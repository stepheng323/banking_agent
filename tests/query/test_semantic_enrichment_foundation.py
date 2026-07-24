from __future__ import annotations

from datetime import date

import pytest

from banking.transactions.query.handlers.insight import handle_insight
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import QueryExtractionResult, QueryTimeRange, TimeReference
from banking.transactions.query.models.operations import AnalyzeOperation, QueryRequest, VarianceDriversSpec
from banking.transactions.query.presentation.surface_builder import apply_selection_payload_to_query
from banking.transactions.query.services.analysis.kernel.contracts import Dimension
from banking.transactions.query.services.analysis.kernel.metrics import _build_variance_drivers
from banking.transactions.query.services.analysis.semantic_enrichment import infer_semantic_values
from banking.transactions.query.services.parsing.parser import QueryParser
from shared.clients.abstractions.banking import TransactionData
from tests.query.factories import analyze_request, query_scope


def test_deterministic_enrichment_keeps_event_type_separate_from_category() -> None:
    values = infer_semantic_values(
        narration="EASEMONI(OPAY MFB) loan disbursement",
        direction="credit",
    )

    assert values.entity_type == "lender"
    assert values.event_type == "loan_disbursement"
    assert values.cash_flow_class == "financing"


def test_economic_event_semantics_cover_investment_without_calling_an_llm() -> None:
    values = infer_semantic_values(
        narration="Piggyvest savings contribution",
        direction="debit",
    )

    assert values.entity_type == "investment_platform"
    assert values.event_type == "investment_contribution"
    assert values.cash_flow_class == "investing"


def test_variance_spec_round_trips_through_query_request() -> None:
    request = analyze_request(query_scope(date(2026, 7, 1), date(2026, 7, 21)))
    restored = QueryRequest.model_validate(request.model_dump(mode="json"))
    assert restored == request


def test_variance_categories_do_not_double_count_counterparty_evidence() -> None:
    from banking.transactions.query.services.analysis.kernel.contracts import (
        DimensionBreakdown,
    )

    current = DimensionBreakdown(
        dimension=Dimension.CATEGORY,
        metric="spending",
        basis="ledger_transactions",
        total=25_000,
        buckets=[
            {"key": "transport", "label": "Transport", "value": 20_000, "count": 1},
            {"key": "food", "label": "Food", "value": 5_000, "count": 1},
        ],
    )
    baseline = DimensionBreakdown(
        dimension=Dimension.CATEGORY,
        metric="spending",
        basis="ledger_transactions",
        total=10_000,
        buckets=[
            {"key": "transport", "label": "Transport", "value": 5_000, "count": 1},
            {"key": "food", "label": "Food", "value": 5_000, "count": 1},
        ],
    )

    drivers, outliers = _build_variance_drivers(current, baseline, evidence_limit=5)

    assert len(drivers) == 1
    assert len(outliers) == 0
    assert drivers[0].key == "transport"
    assert drivers[0].label == "Transport"
    assert drivers[0].current_value == 20_000
    assert drivers[0].baseline_value == 5_000
    assert drivers[0].absolute_delta == 15_000


def test_compiler_accepts_the_registered_variance_insight_without_another_llm_call() -> None:
    parser = QueryParser(llm=object())
    extraction = QueryExtractionResult(
        intent=QueryIntent.INSIGHT,
        insight={"type": "variance_drivers"},
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
    )

    result = parser.compile_extraction(extraction, today=date(2026, 7, 21), language="en")

    assert result.query_request is not None
    assert result.query_request["operation"]["kind"] == "analyze"
    assert result.query_request["operation"]["analysis"]["type"] == "variance_drivers"


class _VarianceProvider:
    async def get_transactions(self, _account_id: str, *, start_date: str, **_kwargs: object) -> list[TransactionData]:
        if start_date == "2026-07-10":
            return [
                TransactionData(
                    transaction_id="current-transport",
                    date="2026-07-11",
                    narration="Uber trip",
                    amount=20_000,
                    transaction_type="debit",
                    category="transport",
                )
            ]
        return [
            TransactionData(
                transaction_id="baseline-transport",
                date="2026-07-09",
                narration="Uber trip",
                amount=5_000,
                transaction_type="debit",
                category="transport",
            )
        ]


@pytest.mark.asyncio
async def test_variance_handler_returns_a_deterministic_category_driver() -> None:
    contract = analyze_request(
        query_scope(date(2026, 7, 10), date(2026, 7, 11)),
        VarianceDriversSpec(analysis_basis="ledger_transactions"),
    )

    result = await handle_insight(_VarianceProvider(), contract, "gtb", ["gtb"], language="en")

    assert result.interpretation is not None
    assert result.interpretation["spending"]["absolute_delta"] == 15_000
    assert result.items is not None
    assert result.items[0].metadata is not None
    assert result.items[0].metadata["label"] == "Transport"


@pytest.mark.asyncio
async def test_variance_driver_selection_executes_grounded_evidence_query() -> None:
    contract = analyze_request(
        query_scope(date(2026, 7, 10), date(2026, 7, 11)),
        VarianceDriversSpec(analysis_basis="ledger_transactions", dimensions=["category"]),
    )
    result = await handle_insight(_VarianceProvider(), contract, "gtb", ["gtb"], language="en")
    assert result.surface_view is not None

    evidence_contract = apply_selection_payload_to_query(
        contract,
        result.surface_view.items[0].payload,
        continuation_type="show_evidence",
    )
    evidence_result = await handle_insight(
        _VarianceProvider(),
        evidence_contract,
        "gtb",
        ["gtb"],
        language="en",
    )

    assert isinstance(evidence_contract.operation, AnalyzeOperation)
    assert evidence_contract.operation.analysis.evidence is not None
    assert evidence_result.items is not None
    assert [item.id for item in evidence_result.items] == ["current-transport"]
