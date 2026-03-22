from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticDecision
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
from shared.i18n import render_message


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise NotImplementedError


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _analytics_frame(
    *,
    frame_id: str,
    turn_index: int,
    start: date,
    end: date,
    amount: float,
    count: int,
) -> QueryFrame:
    return QueryFrame(
        frame_id=frame_id,
        turn_index=turn_index,
        query_contract=QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=start, end=end, granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                aggregation=Aggregation(type="sum"),
            )
        ),
        summary_text=f"You spent ₦{amount:,.0f} on mum.",
        interpretation={"intent": "analytics_summary"},
        surface_type=SurfaceType.SUMMARY,
        surface_context={"type": "spending_total"},
        facts=QueryFrameFacts(metric_kind="amount", amount=amount, count=count, direction="debit"),
    )


def _transaction_list_frame(
    *,
    frame_id: str,
    turn_index: int,
    start: date,
    end: date,
) -> QueryFrame:
    return QueryFrame(
        frame_id=frame_id,
        turn_index=turn_index,
        query_contract=QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=start, end=end, granularity="month"),
                filters=Filters(transaction_type="debit"),
                result_limit=5,
                result_reference="latest",
            )
        ),
        summary_text="Transactions — Mar 01–Mar 19",
        interpretation={"intent": "transaction_list"},
        surface_type=SurfaceType.LIST,
        surface_context={"type": "transactions"},
        facts=QueryFrameFacts(metric_kind="transactions", count=5, direction="debit"),
    )


@pytest.mark.asyncio
async def test_grounded_compare_both_compiles_time_comparison_contract() -> None:
    step = ExtractionStep(_DummyLLM())
    first = _analytics_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 16),
        end=date(2026, 3, 19),
        amount=60000.0,
        count=2,
    )
    second = _analytics_frame(
        frame_id="qf_2",
        turn_index=2,
        start=date(2026, 3, 9),
        end=date(2026, 3, 15),
        amount=64000.0,
        count=4,
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="grounded_query",
            grounded_operation="compare_frames",
            referenced_frame_ids=["qf_1", "qf_2"],
            confidence=0.96,
            reason="ground_compare_both",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "compare both", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": second.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [first.model_dump(), second.model_dump()],
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TIME_COMPARISON
    assert query_contract.comparison is not None
    assert query_contract.comparison.mode == "explicit_range"
    assert query_contract.comparison.explicit_range is not None
    assert query_contract.comparison.explicit_range.start == date(2026, 3, 9)
    assert query_contract.normalized_query.filters is not None
    assert query_contract.normalized_query.filters.merchant == ["mum"]


@pytest.mark.asyncio
async def test_grounded_which_one_was_higher_uses_memory_answer() -> None:
    step = ExtractionStep(_DummyLLM())
    first = _analytics_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 16),
        end=date(2026, 3, 19),
        amount=60000.0,
        count=2,
    )
    second = _analytics_frame(
        frame_id="qf_2",
        turn_index=2,
        start=date(2026, 3, 9),
        end=date(2026, 3, 15),
        amount=64000.0,
        count=4,
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="memory_answer",
            grounded_operation="compare_frames",
            referenced_frame_ids=["qf_1", "qf_2"],
            confidence=0.93,
            reason="ground_which_higher",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "which one was higher", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": second.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [first.model_dump(), second.model_dump()],
        },
    )

    assert updates["flow_state"] == "complete"
    assert (
        updates["response"]
        == "You spent less (₦4,000) in Mar 16 - Mar 19 compared to Mar 09 - Mar 15."
    )


@pytest.mark.asyncio
async def test_grounded_first_one_reopens_selected_frame() -> None:
    step = ExtractionStep(_DummyLLM())
    first = _analytics_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 16),
        end=date(2026, 3, 19),
        amount=60000.0,
        count=2,
    )
    second = _analytics_frame(
        frame_id="qf_2",
        turn_index=2,
        start=date(2026, 3, 9),
        end=date(2026, 3, 15),
        amount=64000.0,
        count=4,
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="grounded_query",
            grounded_operation="select_frame",
            referenced_frame_ids=["qf_1"],
            confidence=0.94,
            reason="ground_select_frame",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "what about the first one", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": second.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [first.model_dump(), second.model_dump()],
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 16)
    assert query_contract.time_end == date(2026, 3, 19)


@pytest.mark.asyncio
async def test_grounded_show_transactions_for_that_one_compiles_transaction_list() -> None:
    step = ExtractionStep(_DummyLLM())
    first = _analytics_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 16),
        end=date(2026, 3, 19),
        amount=60000.0,
        count=2,
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="grounded_query",
            grounded_operation="show_transactions",
            referenced_frame_ids=["qf_1"],
            confidence=0.95,
            reason="ground_show_transactions",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show transactions for that one", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": first.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [first.model_dump()],
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.normalized_query.aggregation is None
    assert query_contract.time_start == date(2026, 3, 16)
    assert query_contract.normalized_query.filters is not None
    assert query_contract.normalized_query.filters.merchant == ["mum"]


@pytest.mark.asyncio
async def test_grounded_reuse_frame_is_ignored_for_aggregate_over_active_transaction_list() -> None:
    step = ExtractionStep(_DummyLLM())
    active_list = _transaction_list_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 1),
        end=date(2026, 3, 19),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="grounded_query",
            grounded_operation="reuse_frame",
            referenced_frame_ids=["qf_1"],
            confidence=0.93,
            reason="ground_reuse_frame_should_not_block_aggregate",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much debit in total?", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": active_list.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [active_list.model_dump()],
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.normalized_query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.normalized_query.filters is not None
    assert query_contract.normalized_query.filters.transaction_type == "debit"
    assert query_contract.normalized_query.aggregation is not None
    assert query_contract.normalized_query.aggregation.type == "sum"


@pytest.mark.asyncio
async def test_grounded_compare_with_incompatible_frames_requests_clarification() -> None:
    step = ExtractionStep(_DummyLLM())
    analytics = _analytics_frame(
        frame_id="qf_1",
        turn_index=1,
        start=date(2026, 3, 16),
        end=date(2026, 3, 19),
        amount=60000.0,
        count=2,
    )
    incompatible = QueryFrame(
        frame_id="qf_2",
        turn_index=2,
        query_contract=QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 15), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                aggregation=Aggregation(type="breakdown", group_by="category"),
            )
        ),
        summary_text="Breakdown",
        interpretation={"intent": "analytics_summary"},
        facts=QueryFrameFacts(metric_kind="unknown"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="grounded_query",
            grounded_operation="compare_frames",
            referenced_frame_ids=["qf_1", "qf_2"],
            confidence=0.85,
            reason="ground_compare_incompatible",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "compare both", "today": date(2026, 3, 19), "language": "en"},
        {
            "session_active": True,
            "query_contract": analytics.query_contract.model_dump(),
            "query_result": {"items": []},
            "query_frames": [analytics.model_dump(), incompatible.model_dump()],
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
