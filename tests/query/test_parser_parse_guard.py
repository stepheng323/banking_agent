from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    Ambiguity,
    AmbiguityCode,
    ExtractionIntent,
    QueryExtractionResult,
    QueryFilters,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from shared.i18n import render_message


class _DummyStructured:
    def __init__(self, extraction: QueryExtractionResult) -> None:
        self._extraction = extraction

    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return self._extraction.model_copy(deep=True)


class _DummyLLM:
    def __init__(self, extraction: QueryExtractionResult) -> None:
        self._extraction = extraction

    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured(self._extraction)


@pytest.mark.asyncio
async def test_time_comparison_without_explicit_time_returns_needs_input() -> None:
    extraction = QueryExtractionResult(intent=ExtractionIntent.TRANSACTION_LIST)
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "compare my spending",
        today=date(2026, 3, 7),
        language="en",
    )

    assert result.outcome.value == "NEEDS_INPUT"
    assert result.resolver_message == render_message("query.time_comparison.prompt_specify_period", "en")
    assert result.query_contract is None


@pytest.mark.asyncio
async def test_all_time_query_auto_clamps_without_blocking_message() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.ALL_TIME),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "who did I send money to the most all time",
        today=date(2026, 3, 10),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.notices == [render_message("query.notice.clamped_days", "en", {"days_back": 180})]
    assert result.query_contract is not None


@pytest.mark.asyncio
async def test_time_vague_clarify_renders_full_message_not_raw_context() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "How much did I spend last",
        today=date(2026, 3, 13),
        language="en",
    )

    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."


@pytest.mark.asyncio
async def test_latest_matching_transaction_shape_does_not_clarify_time() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        filters=QueryFilters(recipient="Mum"),
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
        time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "How much did I send to mum last",
        today=date(2026, 3, 13),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["result_limit"] == 1
    assert result.query_contract["result_reference"] == "latest"
