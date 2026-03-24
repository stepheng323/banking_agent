from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    Ambiguity,
    AmbiguityCode,
    ExtractionIntent,
    ParserQueryExtraction,
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryTimeRange,
    RequestedCapability,
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


class _TrackingStructured:
    def __init__(self, extraction: object) -> None:
        self._extraction = extraction

    async def ainvoke(self, prompt: str) -> object:
        del prompt
        return self._extraction


class _TrackingLLM:
    def __init__(self, extraction: object) -> None:
        self._extraction = extraction
        self.schema: object | None = None

    def with_structured_output(self, schema: object) -> _TrackingStructured:
        self.schema = schema
        return _TrackingStructured(self._extraction)


@pytest.mark.asyncio
async def test_time_comparison_without_explicit_time_returns_needs_input() -> None:
    extraction = QueryExtractionResult(intent=ExtractionIntent.TIME_COMPARISON)
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
async def test_time_vague_matching_transaction_shape_clarifies_without_llm_latest_item_shape() -> None:
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

    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "analytics_summary"
    assert result.query_contract["result_reference"] is None


@pytest.mark.asyncio
async def test_latest_transaction_query_drops_spurious_narration_negotiation_without_keyword() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        result_limit=1,
        result_reference="latest",
        requested_capabilities=[RequestedCapability.SEARCH_NARRATION_FUZZY],
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "show my last transaction",
        today=date(2026, 3, 13),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.query_contract is not None
    assert result.query_contract["result_limit"] == 1
    assert result.query_contract["result_reference"] == "latest"


@pytest.mark.asyncio
async def test_named_month_without_year_defaults_instead_of_clarifying() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="March (no year specified)")],
        time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, period="march"),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "Show all March transactions",
        today=date(2026, 3, 21),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.query_contract is not None
    assert result.query_contract["time_start"] == date(2026, 3, 1)
    assert result.query_contract["time_end"] == date(2026, 3, 21)


@pytest.mark.asyncio
async def test_named_month_last_year_defaults_without_clarifying() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="march_last_year"),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "Show all March last year transactions",
        today=date(2026, 3, 21),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.query_contract is not None
    assert result.query_contract["time_start"] == date(2025, 3, 1)
    assert result.query_contract["time_end"] == date(2025, 3, 31)


@pytest.mark.asyncio
async def test_parser_binds_minimal_schema_and_inflates_downstream_fields() -> None:
    llm = _TrackingLLM(
        ParserQueryExtraction(
            intent=ExtractionIntent.SPENDING_TOTAL,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
            aggregation=QueryAggregation(type="sum"),
        )
    )
    parser = QueryParser(llm)

    result = await parser.parse(
        "How much did I spend today",
        today=date(2026, 3, 21),
        language="en",
    )

    assert llm.schema is ParserQueryExtraction
    assert result.outcome == ResolverOutcome.OK
    assert result.extraction is not None
    assert result.extraction.raw_query == "How much did I spend today"
    assert result.extraction.query_operation is not None
    assert RequestedCapability.AGGREGATE_SUM in result.extraction.requested_capabilities
    assert RequestedCapability.FILTER_TX_TYPE in result.extraction.requested_capabilities
    assert RequestedCapability.TIME_RELATIVE in result.extraction.requested_capabilities
    assert result.query_contract is not None


@pytest.mark.asyncio
async def test_vague_time_from_minimal_parser_output_derives_ambiguity_locally() -> None:
    llm = _TrackingLLM(
        ParserQueryExtraction(
            intent=ExtractionIntent.SPENDING_TOTAL,
            time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
        )
    )
    parser = QueryParser(llm)

    result = await parser.parse(
        "How much did I spend recently",
        today=date(2026, 3, 21),
        language="en",
    )

    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert result.extraction is not None
    assert result.extraction.ambiguities
    assert result.extraction.ambiguities[0].code == AmbiguityCode.TIME_VAGUE
    assert result.pending_clarification is not None
