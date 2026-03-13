from datetime import date, timedelta

import pytest

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
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
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticDecision


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return QueryExtractionResult(raw_query="fallback")


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


@pytest.mark.asyncio
async def test_parse_new_query_inherits_time_range_for_unspecified_time() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 4)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show my transfers",
    )
    parse_result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=extraction)

    async def _fake_parse(message: str, today: date, language: str = "en") -> QueryParseResult:
        del message, today, language
        return parse_result

    def _fake_convert(extraction: QueryExtractionResult, today: date | None = None) -> NormalizedQuery:
        del extraction
        base_today = today or date.today()
        return NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=base_today - timedelta(days=30), end=base_today),
        )

    step.parser.parse = _fake_parse  # type: ignore[method-assign]
    step.parser.convert_to_normalized = _fake_convert  # type: ignore[method-assign]

    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    updates = await step._parse_new_query(
        {
            "message": "show my transfers",
            "today": today,
            "language": "en",
            "query_session": {
                "session_active": True,
                "query": session_query.model_dump(),
                "query_contract": session_contract.model_dump(),
            },
        }
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == today
    assert query.time_range.end == today


@pytest.mark.asyncio
async def test_parse_new_query_does_not_inherit_when_time_is_explicit() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 4)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="show my transfers this month",
    )
    parse_result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=extraction)

    async def _fake_parse(message: str, today: date, language: str = "en") -> QueryParseResult:
        del message, today, language
        return parse_result

    def _fake_convert(extraction: QueryExtractionResult, today: date | None = None) -> NormalizedQuery:
        del extraction
        base_today = today or date.today()
        return NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=base_today - timedelta(days=7), end=base_today),
        )

    step.parser.parse = _fake_parse  # type: ignore[method-assign]
    step.parser.convert_to_normalized = _fake_convert  # type: ignore[method-assign]

    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    updates = await step._parse_new_query(
        {
            "message": "show my transfers this month",
            "today": today,
            "language": "en",
            "query_session": {
                "session_active": True,
                "query": session_query.model_dump(),
                "query_contract": session_contract.model_dump(),
            },
        }
    )

    query = updates["query_contract"].normalized_query
    assert query.time_range is not None
    assert query.time_range.start == today - timedelta(days=7)
    assert query.time_range.end == today


@pytest.mark.asyncio
async def test_parse_new_query_does_not_inherit_for_standalone_unspecified_question() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 7)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="who did I send money to the most",
    )
    parse_result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=extraction)

    async def _fake_parse(message: str, today: date, language: str = "en") -> QueryParseResult:
        del message, today, language
        return parse_result

    def _fake_convert(extraction: QueryExtractionResult, today: date | None = None) -> NormalizedQuery:
        del extraction
        base_today = today or date.today()
        return NormalizedQuery(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=base_today - timedelta(days=30), end=base_today),
        )

    step.parser.parse = _fake_parse  # type: ignore[method-assign]
    step.parser.convert_to_normalized = _fake_convert  # type: ignore[method-assign]

    session_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    updates = await step._parse_new_query(
        {
            "message": "who did I send money to the most",
            "today": today,
            "language": "en",
            "query_session": {
                "session_active": True,
                "query": session_query.model_dump(),
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
        intent=ExtractionIntent.TRANSACTION_LIST,
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
        return QueryParseResult(outcome=ResolverOutcome.OK, extraction=parsed_extraction)

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
        {
            "message": "who did I send money to the most this week",
            "today": today,
            "language": "en",
        },
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
async def test_time_delta_follow_up_preserves_time_comparison_intent() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_query = NormalizedQuery(
        intent=QueryIntent.TIME_COMPARISON,
        time_range=TimeRange(start=today - timedelta(days=6), end=today),
    )
    session_contract = QueryExecutionContract.from_normalized_query(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            time_range=TimeRange(start=date(2026, 3, 5), end=date(2026, 3, 5)),
            delta_type="time",
            confidence=0.9,
            reason="User asked for yesterday",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "what about yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["query_contract"].intent == QueryIntent.TIME_COMPARISON
    assert updates["query_contract"].normalized_query.intent == QueryIntent.TIME_COMPARISON


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
