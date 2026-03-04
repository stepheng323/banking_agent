from datetime import date, timedelta

import pytest

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    NormalizedQuery,
    QueryExtractionResult,
    QueryIntent,
    QueryParseResult,
    QueryTimeRange,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep


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

    updates = await step._parse_new_query(
        {
            "message": "show my transfers",
            "today": today,
            "language": "en",
            "query_session": {"session_active": True, "query": session_query.model_dump()},
        }
    )

    query = updates["query"]
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

    updates = await step._parse_new_query(
        {
            "message": "show my transfers this month",
            "today": today,
            "language": "en",
            "query_session": {"session_active": True, "query": session_query.model_dump()},
        }
    )

    query = updates["query"]
    assert query.time_range is not None
    assert query.time_range.start == today - timedelta(days=7)
    assert query.time_range.end == today
