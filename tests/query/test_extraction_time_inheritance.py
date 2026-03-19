from datetime import date, timedelta

import pytest

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryParseResult,
    QueryTimeRange,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import ActiveQueryTimeRescopeDecision, QuerySemanticDecision
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
async def test_recipient_drilldown_follow_up_applies_merchant_filter() -> None:
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
    assert updates["query_contract"].normalized_query.filters.merchant == ["Gaines"]


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

    async def _fake_time_rescope_parse(**_: object) -> ActiveQueryTimeRescopeDecision:
        return ActiveQueryTimeRescopeDecision(
            decision="time_only_rescope",
            normalized_time_message="last week",
            has_non_time_scope=False,
        )

    async def _unexpected_reason(_: object) -> QuerySemanticDecision:
        raise AssertionError("time-only rescope should skip the general continuation reasoner")

    step.time_rescope_parser.parse = _fake_time_rescope_parse  # type: ignore[method-assign]
    step.reasoner.reason = _unexpected_reason  # type: ignore[method-assign]

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
async def test_summary_contrastive_yesterday_without_reasoner_time_payload_reparses_message() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_time_rescope_parse(**_: object) -> ActiveQueryTimeRescopeDecision:
        return ActiveQueryTimeRescopeDecision(
            decision="time_only_rescope",
            normalized_time_message="yesterday",
            has_non_time_scope=False,
        )

    step.time_rescope_parser.parse = _fake_time_rescope_parse  # type: ignore[method-assign]

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

    async def _fake_time_rescope_parse(**_: object) -> ActiveQueryTimeRescopeDecision:
        return ActiveQueryTimeRescopeDecision(
            decision="time_only_rescope",
            normalized_time_message="last 3 days",
            has_non_time_scope=False,
        )

    step.time_rescope_parser.parse = _fake_time_rescope_parse  # type: ignore[method-assign]

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
async def test_summary_contrastive_last_week_correction_wrapper_replaces_scope_without_reasoner() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = NormalizedQuery(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_time_rescope_parse(**_: object) -> ActiveQueryTimeRescopeDecision:
        return ActiveQueryTimeRescopeDecision(
            decision="time_only_rescope",
            normalized_time_message="last week",
            has_non_time_scope=False,
        )

    async def _unexpected_reason(_: object) -> QuerySemanticDecision:
        raise AssertionError("correction wrapper should skip the general continuation reasoner")

    step.time_rescope_parser.parse = _fake_time_rescope_parse  # type: ignore[method-assign]
    step.reasoner.reason = _unexpected_reason  # type: ignore[method-assign]

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
