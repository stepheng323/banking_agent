from datetime import date

import pytest

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.compiler import finalize
from banking.transactions.query.models.extraction import (
    Ambiguity,
    AmbiguityCode,
    ParserQueryExtraction,
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryTimeRange,
    RequestedCapability,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.models.operations import QueryRequest, RetrieveOperation, SummarizeOperation
from banking.transactions.query.services.parsing.parser import QueryParser
from shared.observability.llm import LLMCallDeadlineExceeded


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


class _DeadlineStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise LLMCallDeadlineExceeded(role="query_parser", deadline_seconds=15.0)


class _DeadlineLLM:
    model_name = "test-query-model"

    def with_structured_output(self, schema: object) -> _DeadlineStructured:
        del schema
        return _DeadlineStructured()


@pytest.mark.asyncio
async def test_parser_timeout_fails_closed_instead_of_running_an_unfiltered_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(*, event_name: str, **kwargs: object) -> None:
        events.append((event_name, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.compiler.finalize.record_llm_call", _capture)
    parser = QueryParser(_DeadlineLLM())

    result = await parser.parse(
        "Compare food spending this month with last month and show the transactions behind the change",
        today=date(2026, 3, 7),
        language="en",
    )

    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert result.query_request is None
    assert result.resolver_message == render_message("orchestrator.fallback.planner_timeout", "en")
    assert events[-1][0] == "query_parser_llm_call"
    assert events[-1][1]["error_type"] == "LLMCallDeadlineExceeded"


@pytest.mark.asyncio
async def test_time_comparison_without_explicit_time_returns_needs_input() -> None:
    extraction = QueryExtractionResult(intent=QueryIntent.TIME_COMPARISON)
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "compare my spending",
        today=date(2026, 3, 7),
        language="en",
    )

    assert result.outcome.value == "NEEDS_INPUT"
    assert result.resolver_message == render_message("query.time_comparison.prompt_specify_period", "en")
    assert result.query_request is None


@pytest.mark.asyncio
async def test_parser_logs_llm_call_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.compiler.finalize.logger.info", _capture)

    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        raw_query="show my last 3 transfers",
    )
    parser = QueryParser(_DummyLLM(extraction))

    await finalize.parse(
        parser,
        "show my last 3 transfers",
        today=date(2026, 3, 7),
        language="en",
    )

    llm_call_events = [payload for event, payload in events if event == "query_parser_llm_call"]
    assert llm_call_events
    assert llm_call_events[0]["language"] == "en"
    assert isinstance(llm_call_events[0]["prompt_chars"], int)
    assert llm_call_events[0]["prompt_chars"] > 0


@pytest.mark.asyncio
async def test_parser_compiles_requested_evidence_into_a_grounded_two_step_plan() -> None:
    extraction = ParserQueryExtraction(
        intent=QueryIntent.TIME_COMPARISON,
        filters=QueryFilters(transaction_type="debit", category="food"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        comparison={"mode": "explicit_period", "period": "last_month"},
        request_shape="comparison",
        evidence_mode="transactions",
    )
    parser = QueryParser(_TrackingLLM(extraction))

    result = await parser.parse(
        "Compare food spending this month with last month and show the transactions behind the change",
        today=date(2026, 7, 29),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.execution_contract is not None
    assert result.execution_contract["kind"] == "plan"
    assert [step["role"] for step in result.execution_contract["steps"]] == ["primary", "evidence"]
    evidence = result.execution_contract["steps"][1]["request"]
    assert evidence["operation"]["kind"] == "retrieve"
    assert evidence["operation"]["scope"]["predicate"]["categories"] == ["food"]
    assert evidence["operation"]["scope"]["predicate"]["direction"] == "debit"


@pytest.mark.asyncio
async def test_parser_does_not_parse_support_problem_statement_as_query() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(transaction_type="debit"),
    )
    llm = _TrackingLLM(extraction)
    parser = QueryParser(llm)

    result = await parser.parse(
        "I was debited but they didn't receive it",
        today=date(2026, 3, 13),
        language="en",
    )

    assert llm.schema is None
    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert result.query_request is None
    assert result.resolver_message == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_all_time_query_auto_clamps_without_blocking_message() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
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
    assert result.query_request is not None


@pytest.mark.asyncio
async def test_time_vague_clarify_renders_full_message_not_raw_context() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "How much did I spend last",
        today=date(2026, 3, 13),
        language="en",
    )

    assert result.outcome == ResolverOutcome.NEEDS_INPUT
    assert (
        result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."
    )


@pytest.mark.asyncio
async def test_plain_people_query_is_recovered_to_beneficiary_summary_without_people_filter() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient="people"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="show people i sent money to this month",
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "show people i sent money to this month",
        today=date(2026, 3, 30),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, SummarizeOperation)
    assert request.operation.summary.type == "grouped"
    assert request.operation.summary.dimension == "counterparty"
    assert request.operation.scope.predicate.counterparty is None
    assert request.operation.scope.predicate.direction == "debit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "language", "placeholder_recipient"),
    [
        ("who I send money give this month", "pcm", "pesin"),
        ("tani mo ran owo si ni osu yi", "yo", "eniyan"),
        ("onye ka m zigara ego n'onwa a", "ig", "nnata"),
        ("wa na tura wa kudi a wannan watan", "ha", "mutanen"),
        ("tani mo send money to this month", "yo", "eniyan"),
    ],
)
async def test_multilingual_recipient_summary_recovery_stays_grouped_and_clears_placeholder_recipient(
    question: str,
    language: str,
    placeholder_recipient: str,
) -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient=placeholder_recipient),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query=question,
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        question,
        today=date(2026, 3, 30),
        language=language,
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, SummarizeOperation)
    assert request.operation.summary.type == "grouped"
    assert request.operation.summary.dimension == "counterparty"
    assert request.operation.scope.predicate.counterparty is None
    assert request.operation.scope.predicate.direction == "debit"


@pytest.mark.asyncio
async def test_time_vague_matching_transaction_shape_clarifies_without_llm_latest_item_shape() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
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
    assert (
        result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."
    )
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, SummarizeOperation)


@pytest.mark.asyncio
async def test_latest_transaction_query_drops_spurious_narration_negotiation_without_keyword() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
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
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, RetrieveOperation)
    assert request.operation.selection.limit == 1
    assert request.operation.selection.order == "latest"
    assert request.operation.selection.order_explicit is True


@pytest.mark.asyncio
async def test_named_month_without_year_defaults_instead_of_clarifying() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
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
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert request.period is not None
    assert request.period.start == date(2026, 3, 1)
    assert request.period.end == date(2026, 3, 21)


@pytest.mark.asyncio
async def test_named_month_last_year_defaults_without_clarifying() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
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
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert request.period is not None
    assert request.period.start == date(2025, 3, 1)
    assert request.period.end == date(2025, 3, 31)


@pytest.mark.asyncio
async def test_parser_binds_minimal_schema_and_inflates_downstream_fields() -> None:
    llm = _TrackingLLM(
        ParserQueryExtraction(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
            aggregation=QueryAggregation(type="sum"),
            evidence_mode="none",
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
    assert result.extraction.intent is not None
    assert RequestedCapability.AGGREGATE_SUM in result.extraction.requested_capabilities
    assert RequestedCapability.FILTER_TX_TYPE in result.extraction.requested_capabilities
    assert RequestedCapability.TIME_RELATIVE in result.extraction.requested_capabilities
    assert result.query_request is not None


@pytest.mark.asyncio
async def test_vague_time_from_minimal_parser_output_derives_ambiguity_locally() -> None:
    llm = _TrackingLLM(
        ParserQueryExtraction(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
            evidence_mode="none",
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
    assert result.pending_input is not None


@pytest.mark.asyncio
async def test_recent_transaction_list_defaults_to_bounded_30_day_window() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "show my recent transactions",
        today=date(2026, 3, 28),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.pending_input is None
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, RetrieveOperation)
    assert request.operation.scope.period.start == date(2026, 2, 27)
    assert request.operation.scope.period.end == date(2026, 3, 28)


@pytest.mark.asyncio
async def test_day_scoped_singular_transaction_query_normalizes_to_list_query() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        result_limit=1,
        result_reference="latest",
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "show today's transaction",
        today=date(2026, 3, 28),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_request is not None
    request = QueryRequest.model_validate(result.query_request)
    assert isinstance(request.operation, RetrieveOperation)
    assert request.operation.scope.period.start == date(2026, 3, 28)
    assert request.operation.scope.period.end == date(2026, 3, 28)
    assert request.operation.selection.limit is None
    assert request.operation.selection.order_explicit is False
