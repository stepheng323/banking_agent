"""Tests for cross-frame reconciliation."""

from __future__ import annotations

from datetime import date

import pytest

from banking.transactions.query.continuations.reconciliation import (
    ReconciliationResult,
    reconcile_query_answer,
)
from banking.transactions.query.continuations.result_paths import (
    _maybe_rebuild_intent_correction_request,
)
from banking.transactions.query.contracts import CounterpartyConcentrationEvidenceSelection
from banking.transactions.query.models.conversation import QueryScopeDelta
from banking.transactions.query.models.domain import (
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    QueryRequest,
)
from banking.transactions.query.models.operations import (
    AllAccounts,
    AnalyzeOperation,
    CounterpartyConcentrationSpec,
    GroupedSummarySpec,
    QueryScope,
    ResolvedPeriod,
    SummarizeOperation,
    TransactionPredicate,
)
from banking.transactions.query.presentation.surface_builder import apply_selection_payload_to_query


def _scope(start: date, end: date) -> QueryScope:
    return QueryScope(
        period=ResolvedPeriod(start=start, end=end),
        predicate=TransactionPredicate(),
        accounts=AllAccounts(),
    )


def _beneficiary_request(start: date, end: date) -> QueryRequest:
    request = QueryRequest(
        operation=SummarizeOperation(
            scope=_scope(start, end),
            summary=GroupedSummarySpec(
                measure="spending",
                dimension="counterparty",
                rank_by="amount",
                limit=5,
            ),
        ),
    )
    assert request.intent == QueryIntent.BENEFICIARY_SUMMARY
    return request


def _concentration_request(start: date, end: date) -> QueryRequest:
    request = QueryRequest(
        operation=AnalyzeOperation(
            scope=_scope(start, end),
            analysis=CounterpartyConcentrationSpec(measure="spending"),
        ),
    )
    assert request.intent == QueryIntent.INSIGHT
    return request


def _frame(
    frame_id: str,
    request: QueryRequest,
    visible_items: list[dict[str, object]],
) -> QueryFrame:
    return QueryFrame(
        frame_id=frame_id,
        turn_index=1,
        query_request=request,
        summary_text="Summary",
        visible_items=visible_items,
        facts=QueryFrameFacts(),
    )


@pytest.mark.asyncio
async def test_reconcile_explains_spending_vs_transfers_scope() -> None:
    """The Uber case: concentration insight vs. beneficiary summary."""
    current_request = _beneficiary_request(date(2026, 7, 1), date(2026, 7, 31))
    concentration_request = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))
    frame = _frame(
        "qf_1",
        concentration_request,
        [
            {
                "id": "uber",
                "label": "Uber",
                "amount": 45000.0,
                "counterparty": "Uber",
                "direction": "debit",
                "date": "2026-07-15",
                "selection_kind": "summary_scope",
                "entity_type": "counterparty_concentration",
                "selection_payload": {
                    "selection_kind": "summary_scope",
                    "entity_type": "counterparty_concentration",
                    "entity_id": "uber",
                    "label": "Uber",
                    "insight_evidence": CounterpartyConcentrationEvidenceSelection(
                        basis="economic_events",
                        counterparty_key="uber",
                        measure="spending",
                        effective_start="2026-07-01",
                        effective_end="2026-07-31",
                    ).model_dump(mode="json"),
                },
            }
        ],
    )

    result = await reconcile_query_answer(
        session_query_request=current_request,
        query_frames=[frame],
        target_text="uber",
        target_amount=None,
        referenced_frame_ids=None,
        locale="en",
    )

    assert isinstance(result, ReconciliationResult)
    assert result.source_frame_id == "qf_1"
    assert "Uber" in result.response
    assert "spending breakdown" in result.response
    assert "bank transfers" in result.response
    assert result.evidence_payload is not None
    assert result.evidence_payload.entity_type == "counterparty_concentration"
    assert result.source_query_request is not None
    replay = apply_selection_payload_to_query(result.source_query_request, result.evidence_payload)
    assert isinstance(replay.operation, AnalyzeOperation)
    assert replay.operation.analysis.insight_type == "counterparty_concentration"


@pytest.mark.asyncio
async def test_reconcile_returns_clarification_when_target_not_in_frames() -> None:
    """When the challenged entity cannot be found, reconciliation should not invent an answer."""
    current_request = _beneficiary_request(date(2026, 7, 1), date(2026, 7, 31))
    frame = _frame(
        "qf_1",
        _concentration_request(date(2026, 7, 1), date(2026, 7, 31)),
        [{"id": "bolt", "label": "Bolt", "amount": 12000.0, "counterparty": "Bolt", "direction": "debit"}],
    )

    result = await reconcile_query_answer(
        session_query_request=current_request,
        query_frames=[frame],
        target_text="uber",
        target_amount=None,
        referenced_frame_ids=None,
        locale="en",
    )

    assert result.outcome == "clarification"


@pytest.mark.asyncio
async def test_reconcile_matches_by_amount() -> None:
    """Reconciliation can reference a challenged amount rather than a name."""
    current_request = _beneficiary_request(date(2026, 7, 1), date(2026, 7, 31))
    frame = _frame(
        "qf_1",
        _concentration_request(date(2026, 7, 1), date(2026, 7, 31)),
        [{"id": "item", "label": "Unknown", "amount": 50000.0, "counterparty": "Unknown", "direction": "debit"}],
    )

    result = await reconcile_query_answer(
        session_query_request=current_request,
        query_frames=[frame],
        target_text=None,
        target_amount=50000.0,
        referenced_frame_ids=None,
        locale="en",
    )

    assert isinstance(result, ReconciliationResult)
    assert "Unknown" in result.response


@pytest.mark.asyncio
async def test_reconcile_detects_period_mismatch() -> None:
    """When the same entity appears in answers for different periods, explain the period gap."""
    current_request = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))
    concentration_request = _concentration_request(date(2026, 6, 1), date(2026, 6, 30))
    frame = _frame(
        "qf_1",
        concentration_request,
        [{"id": "uber", "label": "Uber", "amount": 45000.0, "counterparty": "Uber", "direction": "debit"}],
    )

    result = await reconcile_query_answer(
        session_query_request=current_request,
        query_frames=[frame],
        target_text="uber",
        target_amount=None,
        referenced_frame_ids=None,
        locale="en",
    )

    assert isinstance(result, ReconciliationResult)
    assert "2026-07" in result.response
    assert "2026-06" in result.response


@pytest.mark.asyncio
async def test_reconcile_requires_a_target() -> None:
    result = await reconcile_query_answer(
        session_query_request=None,
        query_frames=None,
        target_text=None,
        target_amount=None,
        referenced_frame_ids=None,
        locale="en",
    )
    assert result.outcome == "clarification"


@pytest.mark.asyncio
async def test_reconcile_rejects_explicit_stale_frame_without_falling_back() -> None:
    frame = _frame(
        "qf_1",
        _concentration_request(date(2026, 7, 1), date(2026, 7, 31)),
        [{"id": "uber", "label": "Uber", "amount": 45000.0}],
    )

    result = await reconcile_query_answer(
        session_query_request=None,
        query_frames=[frame],
        target_text="uber",
        target_amount=None,
        referenced_frame_ids=["qf_expired"],
        locale="en",
    )

    assert result.outcome == "expired"


@pytest.mark.asyncio
async def test_reconcile_requires_text_and_amount_when_both_are_supplied() -> None:
    frame = _frame(
        "qf_1",
        _concentration_request(date(2026, 7, 1), date(2026, 7, 31)),
        [{"id": "bolt", "label": "Bolt", "amount": 50000.0}],
    )

    result = await reconcile_query_answer(
        session_query_request=None,
        query_frames=[frame],
        target_text="uber",
        target_amount=50000.0,
        referenced_frame_ids=None,
        locale="en",
    )

    assert result.outcome == "clarification"


@pytest.mark.asyncio
async def test_reconcile_applies_correction_to_exact_source_frame_contract() -> None:
    current = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))
    source = _concentration_request(date(2026, 6, 1), date(2026, 6, 30))
    frame = _frame(
        "qf_1",
        source,
        [{"id": "uber", "label": "Uber", "amount": 45000.0}],
    )

    result = await reconcile_query_answer(
        session_query_request=current,
        query_frames=[frame],
        target_text="uber",
        target_amount=None,
        referenced_frame_ids=["qf_1"],
        correction_delta=QueryScopeDelta(direction_mutation="replace", direction="credit"),
        locale="en",
    )

    assert result.corrected_query_request is not None
    assert result.corrected_query_request.time_start == date(2026, 6, 1)
    assert result.corrected_query_request.time_end == date(2026, 6, 30)
    assert result.corrected_query_request.filters is not None
    assert result.corrected_query_request.filters.transaction_type == "credit"


def test_intent_correction_rebuilds_concentration_to_beneficiary_summary() -> None:
    concentration = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))

    corrected = _maybe_rebuild_intent_correction_request(
        "I meant who?",
        target_text=None,
        session_query_request=concentration,
    )

    assert corrected is not None
    assert corrected.intent == QueryIntent.BENEFICIARY_SUMMARY
    op = corrected.operation
    assert isinstance(op, SummarizeOperation)
    assert op.summary.dimension == "counterparty"
    assert op.summary.statistic == "count"
    assert op.summary.limit == 1
    assert corrected.scope.predicate.direction == "debit"
    assert corrected.scope.period == ResolvedPeriod(start=date(2026, 7, 1), end=date(2026, 7, 31))


def test_intent_correction_skips_non_assertive_messages() -> None:
    concentration = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))
    assert (
        _maybe_rebuild_intent_correction_request(
            "who did I send money to",
            target_text=None,
            session_query_request=concentration,
        )
        is None
    )


def test_intent_correction_skips_non_concentration_requests() -> None:
    beneficiary = _beneficiary_request(date(2026, 7, 1), date(2026, 7, 31))
    assert (
        _maybe_rebuild_intent_correction_request(
            "I meant who?",
            target_text=None,
            session_query_request=beneficiary,
        )
        is None
    )


def test_intent_correction_matches_person_target_text_fallback() -> None:
    concentration = _concentration_request(date(2026, 7, 1), date(2026, 7, 31))
    corrected = _maybe_rebuild_intent_correction_request(
        "I meant that",
        target_text="who",
        session_query_request=concentration,
    )
    assert corrected is not None
    assert corrected.intent == QueryIntent.BENEFICIARY_SUMMARY
