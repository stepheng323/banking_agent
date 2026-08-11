"""Optional live-model evals for the query semantic reasoner."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest

from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    Filters,
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    QueryRequest,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import QueryExtractionResult
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
from banking.transactions.query.services.reasoning.models import (
    QuerySemanticDecision,
    SemanticReasonerContext,
)
from banking.transactions.query.services.reasoning.reasoner import QuerySemanticReasoner
from tests.query.factories import make_pending_input, make_query_request


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 20)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


def _contract(query: QueryRequest) -> QueryRequest:
    return query.model_copy(deep=True)


def _live_chat_model() -> Any:
    if os.getenv("QUERY_REASONER_LIVE_EVAL") != "1":
        pytest.skip("set QUERY_REASONER_LIVE_EVAL=1 to run live query-reasoner evals")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for live query-reasoner evals")

    chat_openai = pytest.importorskip("langchain_openai")
    model_name = os.getenv("QUERY_REASONER_LIVE_MODEL", os.getenv("PLANNER_REPLAY_PARITY_MODEL", "gpt-4o-mini"))
    return chat_openai.ChatOpenAI(model=model_name, temperature=0, seed=42)


def _active_transaction_list_context(message: str, *, language: str = "en") -> SemanticReasonerContext:
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language=language,
        query_request=_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 20), granularity="month"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
            )
        ),
        items=[
            QueryResultItem(
                id="txn-1",
                description="Transfer to Mum",
                amount=50000.0,
                date=date(2026, 3, 17),
                metadata={"transaction_type": "debit", "recipient_name": "Mum", "bank_name": "Zenith Bank"},
            ),
            QueryResultItem(
                id="txn-2",
                description="Payment to Mum",
                amount=10000.0,
                date=date(2026, 3, 16),
                metadata={"transaction_type": "debit", "recipient_name": "Mum", "bank_name": "Zenith Bank"},
            ),
        ],
        surface_view=SurfaceView(
            mode=SurfaceViewMode.TRANSACTION_LIST,
            context={"type": "transaction_list", "count": 5, "total_results": 5, "has_more": False},
        ),
    )


def _active_summary_context(message: str) -> SemanticReasonerContext:
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language="en",
        query_request=_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 20), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
            )
        ),
        surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context={"type": "spending_total"}),
    )


def _pending_clarification_context(message: str) -> SemanticReasonerContext:
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language="en",
        pending_input=make_pending_input(
            original_query="How much did I spend last",
            original_extraction=QueryExtractionResult(raw_query="How much did I spend last"),
            resolver_message="What time period did you mean by last?",
            language="en",
        ),
    )


def _reconciliation_context(message: str) -> SemanticReasonerContext:
    """Current surface is a beneficiary summary; prior frame has counterparty concentration with Uber."""
    period = ResolvedPeriod(start=date(2026, 3, 1), end=date(2026, 3, 31))
    scope = QueryScope(period=period, accounts=AllAccounts(), predicate=TransactionPredicate())
    beneficiary_request = QueryRequest(
        operation=SummarizeOperation(
            scope=scope,
            summary=GroupedSummarySpec(measure="spending", dimension="counterparty", rank_by="amount", limit=5),
        ),
    )
    concentration_request = QueryRequest(
        operation=AnalyzeOperation(
            scope=scope,
            analysis=CounterpartyConcentrationSpec(measure="spending"),
        ),
    )
    prior_frame = QueryFrame(
        frame_id="qf_1",
        turn_index=1,
        query_request=concentration_request,
        summary_text="Your largest spending counterparty is Uber at 6.3% of the total.",
        visible_items=[
            {
                "id": "uber-concentration",
                "label": "Uber",
                "amount": 45000.0,
                "counterparty": "Uber",
                "direction": "debit",
                "date": "2026-03-15",
                "selection_kind": "summary_scope",
                "entity_type": "counterparty_concentration",
            }
        ],
        facts=QueryFrameFacts(),
    )
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language="en",
        query_request=beneficiary_request,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            context={"view": "beneficiary_summary", "type": "grouped_summary"},
        ),
        items=[
            QueryResultItem(
                id="mum",
                description="Mum",
                amount=150000.0,
                date=date(2026, 3, 18),
                metadata={"recipient_name": "Mum", "transaction_type": "debit", "count": 3},
            ),
            QueryResultItem(
                id="dad",
                description="Dad",
                amount=60000.0,
                date=date(2026, 3, 17),
                metadata={"recipient_name": "Dad", "transaction_type": "debit", "count": 2},
            ),
        ],
        query_frames=[prior_frame],
    )


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_pending_clarification() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_pending_clarification_context("last 3 days"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "clarification_answer"
    # gpt-4o-mini may return the resolved period in either time_period or clarification_patch.
    assert (result.time_period is not None and "3" in result.time_period) or (
        result.clarification_patch is not None
        and result.clarification_patch.time_range is not None
        and result.clarification_patch.time_range.days_back == 3
    )


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_aggregate_followup() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_transaction_list_context("How much total"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "continuation"
    assert result.continuation_type == "aggregate"
    assert result.followup_intent == "refine_existing"


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_pidgin_aggregate_followup() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_transaction_list_context("wetin be total", language="Pidgin"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "continuation"
    assert result.continuation_type == "aggregate"
    assert result.followup_intent == "refine_existing"


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_time_rescope_followup() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_summary_context("What about last week"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "continuation"
    assert result.continuation_type == "time_delta"
    # gpt-4o-mini sometimes omits followup_intent; the continuation_type is the authoritative signal.
    assert result.followup_intent in {None, "replace_scope"}


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_fresh_restatement_becomes_new_query() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_summary_context("Show my credit transactions this month"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "new_query"
    assert result.extraction is not None


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_recipient_summary_followup_becomes_new_query() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_summary_context("Who did I send money to this month"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "new_query"
    assert result.extraction is not None


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_dismissive_turn_ends_session() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_summary_context("get out"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "end_session"


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_reconcile_challenge_finds_prior_frame() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_reconciliation_context("So where did you get uber?"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "continuation"
    assert result.continuation_type == "reconcile"
    assert result.target_text is not None
    assert "uber" in result.target_text.lower()
