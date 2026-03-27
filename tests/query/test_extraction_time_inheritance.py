from datetime import date, timedelta
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryParseResult,
    QueryResultItem,
    QueryTimeRange,
    ResolverOutcome,
    ResultSurface,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticDecision
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
from shared.i18n import render_message


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return QueryExtractionResult(raw_query="fallback")


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _ok_result(extraction: QueryExtractionResult, query: NormalizedQuery) -> QueryParseResult:
    contract = QueryExecutionContract.from_normalized_query(query)
    return QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=extraction,
        query_contract=contract.model_dump(mode="json"),
    )


@pytest.mark.asyncio
async def test_parse_new_query_does_not_inherit_time_range_for_unspecified_time() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 4)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show my transfers",
    )
    parsed_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=30), end=today),
    )
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.95,
            reason="fresh_unspecified_query",
            extraction=extraction,
        )

    def _fake_resolve_existing(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    updates = await step._parse_new_query(
        {
            "message": "show my transfers",
            "today": today,
            "language": "en",
            "query_session": {
                "session_active": True,
                "query_contract": session_contract.model_dump(),
            },
        }
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == today - timedelta(days=30)
    assert query.time_range.end == today


@pytest.mark.asyncio
async def test_recipient_ranking_followup_reparses_as_new_beneficiary_summary_query() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 7)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week", days_back=5),
        raw_query="who did I send money to the most this week",
    )

    def _fake_resolve_existing(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        query = NormalizedQuery(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 2), end=date(2026, 3, 7)),
        )
        return _ok_result(parsed_extraction, query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.99,
            reason="recipient_ranking_followup",
            extraction=extraction,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "who did I send money to the most this week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.normalized_query.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.time_start == date(2026, 3, 2)
    assert query_contract.time_end == today


@pytest.mark.asyncio
async def test_replace_scope_resets_pagination_and_preserves_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(merchant=["Mum"], transaction_type="debit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
            delta_type="time",
            confidence=0.96,
            reason="llm_replace_scope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 8)
    assert query.time_range.end == date(2026, 3, 14)
    assert query.filters is not None
    assert query.filters.merchant == ["Mum"]
    assert query.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_continue_pagination_only_advances_page_without_scope_mutation() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="continue_pagination",
            confidence=0.97,
            reason="llm_continue_pagination",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "more", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["current_page"] == 2
    assert "query_contract" not in updates


@pytest.mark.asyncio
async def test_invalid_time_delta_and_continue_pagination_combo_requests_clarification() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="continue_pagination",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
            delta_type="time",
            confidence=0.9,
            reason="invalid_pagination_time_combo",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_recipient_drilldown_follow_up_applies_counterparty_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 10)
    session_query = NormalizedQuery(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="recipient_drill_down",
            followup_intent="none",
            recipient_name="Gaines",
            delta_type="filter",
            confidence=0.99,
            reason="deterministic_recipient_drill_down",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Gaines", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "recipient_drill_down"
    assert updates["query_contract"].normalized_query.filters is not None
    assert updates["query_contract"].normalized_query.filters.counterparty == ["Gaines"]


@pytest.mark.asyncio
async def test_show_me_follow_up_converts_summary_to_transactions_when_explicitly_requested() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="refine_existing",
            confidence=1.0,
            reason="llm_show_underlying_transactions",
            delta_type="none",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["query_contract"].normalized_query.intent == QueryIntent.TRANSACTION_LIST
    assert updates["query_contract"].normalized_query.time_range is not None
    assert updates["query_contract"].normalized_query.time_range.start == date(2026, 3, 1)
    assert updates["query_contract"].normalized_query.time_range.end == today
    assert updates["current_page"] == 0
    assert updates["resolver_message"] is None


@pytest.mark.asyncio
async def test_show_me_follow_up_does_not_convert_summary_on_pagination_intent() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="continue_pagination",
            confidence=0.95,
            reason="pagination_on_summary_is_invalid",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_weekly_summary_show_them_then_only_this_weeks_replaces_scope_and_resets_pagination() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 2, 17), end=today),
        filters=Filters(transaction_type="debit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    decisions = iter(
        [
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="show_more",
                followup_intent="refine_existing",
                confidence=0.99,
                reason="llm_show_underlying_transactions",
            ),
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="time_delta",
                followup_intent="replace_scope",
                time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
                delta_type="time",
                confidence=0.97,
                reason="llm_replace_scope_this_week_possessive",
            ),
        ]
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return next(decisions)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    first_updates = await step._handle_continuation(
        {"message": "Show them", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert first_updates["query_contract"].normalized_query.intent == QueryIntent.TRANSACTION_LIST
    assert first_updates["query_contract"].normalized_query.time_range is not None
    assert first_updates["query_contract"].normalized_query.time_range.start == date(2026, 2, 17)
    assert first_updates["query_contract"].normalized_query.time_range.end == today
    assert first_updates["current_page"] == 0

    second_updates = await step._handle_continuation(
        {"message": "Only this week's", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": first_updates["query_contract"].model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query = second_updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.TRANSACTION_LIST
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 16)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert second_updates["current_page"] == 0
    assert second_updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_replaces_scope_and_preserves_recipient_and_debit_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.97,
            reason="llm_replace_scope_last_week",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 9)
    assert query.time_range.end == date(2026, 3, 15)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_logs_semantic_reasoner_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.97,
            reason="llm_replace_scope_last_week",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert (
        "query_continuation_resolution",
        {
            "path": "semantic_reasoner",
            "semantic_decision": "continuation",
            "continuation_type": "time_delta",
            "followup_intent": "replace_scope",
            "delta_type": None,
        },
    ) in events


@pytest.mark.asyncio
async def test_low_confidence_unclear_last_week_recovers_via_time_rescope_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 9)
    assert query.time_range.end == date(2026, 3, 15)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert (
        "query_continuation_resolution",
        {
            "path": "time_rescope_recovery",
            "trigger_reason": "low_confidence_unclear",
            "recovered": True,
            "session_has_query_contract": True,
            "resolved_time_range": True,
            "resolved_time_start": "2026-03-09",
            "resolved_time_end": "2026-03-15",
            "preserved_query_shape": True,
            "skip_reason": None,
        },
    ) in events


@pytest.mark.asyncio
async def test_grounded_ask_clarify_last_week_recovers_via_time_rescope_recovery() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="ask_clarify",
            confidence=0.84,
            reason="grounded_reference_ambiguous",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 9)
    assert query.time_range.end == date(2026, 3, 15)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_aggregate_followup_without_extraction_preserves_active_query_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.93,
            reason="llm_total_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much total", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 1)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert query.aggregation is not None
    assert query.aggregation.type == "sum"
    assert query.result_limit is None
    assert query.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_income_followup_after_credit_list_preserves_active_credit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_income_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 1)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "credit"
    assert query.aggregation is not None
    assert query.aggregation.type == "sum"
    assert query.result_limit is None
    assert query.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_income_repair_followup_after_credit_list_preserves_active_credit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.91,
            reason="llm_income_repair_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 1)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "credit"
    assert query.aggregation is not None
    assert query.aggregation.type == "sum"
    assert query.result_limit is None
    assert query.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_income_vs_spending_followup_compiles_transaction_type_breakdown() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_income_vs_spending_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Compare the income vs spending", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 1)
    assert query.time_range.end == today
    assert query.filters is None or query.filters.transaction_type is None
    assert query.aggregation is not None
    assert query.aggregation.type == "breakdown"
    assert query.aggregation.group_by == "transaction_type"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_low_confidence_unclear_income_followup_clarifies_without_parser_reparse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)
    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_income_followup",
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("low-confidence unclear follow-up should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What's my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["flow_state"] == "parsing"


@pytest.mark.asyncio
async def test_unclear_income_repair_followup_uses_reasoner_compiler_without_parser_parse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="I mean my income this month",
    )
    parsed_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.76,
            reason="repair_phrase_not_grounded",
            extraction=extraction,
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("compiler-safe reasoner extraction should not call parser.parse")

    def _fake_compile(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert parsed_extraction.raw_query == "I mean my income this month"
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]
    step.parser.compile_extraction = _fake_compile  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 1)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "credit"
    assert updates["current_page"] == 0
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_unclear_highest_single_transfer_repair_clarifies_without_explicit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="I mean my highest single transfer",
    )
    parsed_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="largest", limit=1),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.31,
            reason="repair_phrase_not_grounded",
        )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "I mean my highest single transfer"
        return _ok_result(extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my highest single transfer", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
    assert updates["flow_state"] == "parsing"
    assert updates["session_active"] is True


@pytest.mark.asyncio
async def test_unclear_credit_pivot_followup_clarifies_without_grounded_reasoner_resolution() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        raw_query="What about credit",
    )
    parsed_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
        filters=Filters(transaction_type="credit"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.32,
            reason="credit_pivot_not_grounded",
        )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "What about credit"
        return _ok_result(extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about credit", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
    assert updates["flow_state"] == "parsing"
    assert updates["session_active"] is True


@pytest.mark.asyncio
async def test_dismissive_end_session_uses_localized_de_escalation_reply() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today, granularity="day"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="end_session",
            confidence=0.97,
            reason="dismissive_stop_helping_turn",
            end_session_kind="dismissive",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "get out", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert updates["response"] == render_message("query.session.dismissive_goodbye", "en")
    assert updates["session_active"] is False
    assert updates["flow_state"] == "complete"


@pytest.mark.asyncio
async def test_plain_recipient_summary_followup_reparses_as_new_beneficiary_summary_query() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who did I send money to this month",
    )

    def _fake_resolve_existing(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        query = NormalizedQuery(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21), granularity="month"),
            filters=Filters(transaction_type="debit"),
        )
        return _ok_result(parsed_extraction, query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.95,
            reason="recipient_summary_followup",
            extraction=extraction,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Who did I send money to this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.normalized_query.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.normalized_query.filters is not None
    assert query_contract.normalized_query.filters.transaction_type == "debit"


@pytest.mark.asyncio
async def test_show_me_logs_semantic_reasoner_continuation_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="refine_existing",
            confidence=1.0,
            reason="llm_show_underlying_transactions",
            delta_type="none",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert (
        "query_continuation_resolution",
        {
            "path": "semantic_reasoner",
            "semantic_decision": "continuation",
            "continuation_type": "show_more",
            "followup_intent": "refine_existing",
            "delta_type": "none",
        },
    ) in events


@pytest.mark.asyncio
async def test_summary_contrastive_yesterday_without_reasoner_time_payload_reparses_message() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
            confidence=0.96,
            reason="llm_replace_scope_yesterday",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 18)
    assert query.time_range.end == date(2026, 3, 18)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_three_days_without_reasoner_time_payload_reparses_message() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_period="last 3 days",
            confidence=0.96,
            reason="llm_replace_scope_last_three_days",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last 3 days", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 16)
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_only_today_replaces_scope_and_preserves_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(transaction_type="debit", merchant=["Mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=today, end=today, granularity="day"),
            delta_type="time",
            confidence=0.96,
            reason="llm_replace_scope_today",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "only today", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == today
    assert query.time_range.end == today
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["Mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_single_item_contrastive_yesterday_preserves_latest_shape() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 16), end=today),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
            confidence=0.94,
            reason="llm_single_item_yesterday",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Salary from Acme Corp",
                        amount=950000.0,
                        date=date(2026, 3, 17),
                        metadata={"type": "credit", "bank_name": "First Bank"},
                    ).model_dump(mode="json")
                ]
            },
            "surface": ResultSurface(type="single_item", items=[], context={"type": "single_transaction"}).model_dump(),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 18)
    assert query.time_range.end == date(2026, 3, 18)
    assert query.result_limit == 1
    assert query.result_reference == "latest"


@pytest.mark.asyncio
async def test_single_item_grounded_ask_clarify_recovers_to_yesterday_time_rescope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 16), end=today),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            answer_mode="ask_clarify",
            confidence=0.91,
            reason="llm_single_item_ask_clarify",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "No transaction yesterday?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Salary from Acme Corp",
                        amount=950000.0,
                        date=date(2026, 3, 17),
                        metadata={"type": "credit", "bank_name": "First Bank"},
                    ).model_dump(mode="json")
                ]
            },
            "surface": ResultSurface(type="single_item", items=[], context={"type": "single_transaction"}).model_dump(),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "time_delta"
    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 18)
    assert query.time_range.end == date(2026, 3, 18)
    assert query.result_limit == 1
    assert query.result_reference == "latest"
    assert (
        "query_single_item_followup",
        {
            "surface_type": "single_item",
            "session_mode": "active_result",
            "continuation_type": "time_delta",
            "followup_outcome": "time_rescope_query",
            "semantic_decision": "continuation",
        },
    ) in events


@pytest.mark.asyncio
async def test_summary_last_month_only_replaces_scope_and_preserves_debit_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 1, 1), end=today),
        filters=Filters(transaction_type="debit"),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month"),
            delta_type="time",
            confidence=0.98,
            reason="llm_replace_scope_last_month",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last month only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 4,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 2, 1)
    assert query.time_range.end == date(2026, 2, 28)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_correction_wrapper_replaces_scope_via_reasoner() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.98,
            reason="llm_replace_scope_last_week_correction",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "no, i meant last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == date(2026, 3, 9)
    assert query.time_range.end == date(2026, 3, 15)
    assert query.filters is not None
    assert query.filters.transaction_type == "debit"
    assert query.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_low_confidence_unclear_followup_requests_clarification() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
