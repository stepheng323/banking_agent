"""Optional live-model evals for the query semantic reasoner."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.reasoner import (
    QuerySemanticDecision,
    QuerySemanticReasoner,
    SemanticReasonerContext,
)


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
        query_contract=QueryExecutionContract(
            intent=QueryIntent.TRANSACTION_LIST,
            time_start=date(2026, 3, 1),
            time_end=date(2026, 3, 20),
            normalized_query=NormalizedQuery(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 20), granularity="month"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
            ),
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
        surface=ResultSurface(
            type=SurfaceType.LIST,
            items=[],
            context={"type": "transaction_list", "count": 5, "total_results": 5, "has_more": False},
        ),
    )


def _active_summary_context(message: str) -> SemanticReasonerContext:
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language="en",
        query_contract=QueryExecutionContract(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_start=date(2026, 3, 16),
            time_end=date(2026, 3, 20),
            normalized_query=NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 20), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
            ),
        ),
        surface=ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"type": "spending_total"}),
    )


def _pending_clarification_context(message: str) -> SemanticReasonerContext:
    return SemanticReasonerContext(
        message=message,
        today=date(2026, 3, 20),
        language="en",
        pending_clarification=PendingClarificationState(
            original_query="How much did I spend last",
            current_intent="spending_total",
            original_extraction=QueryExtractionResult(raw_query="How much did I spend last"),
            resolver_message="What time period did you mean by last?",
            language="en",
        ),
    )


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_pending_clarification() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_pending_clarification_context("last 3 days"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "clarification_answer"
    assert result.time_period is not None
    assert "3" in result.time_period


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
    assert result.followup_intent == "replace_scope"


@pytest.mark.asyncio
async def test_query_reasoner_live_eval_fresh_restatement_becomes_new_query() -> None:
    reasoner = QuerySemanticReasoner(_live_chat_model())

    result = await reasoner.reason(_active_summary_context("Show my credit transactions this month"))

    assert isinstance(result, QuerySemanticDecision)
    assert result.decision == "new_query"
    assert result.extraction is not None
