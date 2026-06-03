from datetime import date, timedelta

import pytest

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.compiler import finalize
from banking.transactions.query.models.extraction import (
    Ambiguity,
    AmbiguityCode,
    ExtractionIntent,
    FactQueryKind,
    ParserQueryExtraction,
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryRequestShape,
    QueryTimeRange,
    RequestedCapability,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.services.parsing.parser import QueryParser


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
async def test_parser_logs_llm_call_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.compiler.finalize.logger.info", _capture)

    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
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
async def test_parser_does_not_parse_support_problem_statement_as_query() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
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
    assert result.query_contract is None
    assert result.resolver_message == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_parser_keeps_explicit_latest_status_query_in_query_domain() -> None:
    llm = _TrackingLLM(QueryExtractionResult())
    parser = QueryParser(llm)

    result = await parser.parse(
        "What is the status of my last transaction?",
        today=date(2026, 3, 13),
        language="en",
    )

    assert llm.schema is None
    assert result.outcome == ResolverOutcome.OK
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["answer_fact_field"] == "status"
    assert result.query_contract["result_reference"] == "latest"


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
    assert (
        result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."
    )


@pytest.mark.asyncio
async def test_plain_people_query_is_recovered_to_beneficiary_summary_without_people_filter() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
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
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "beneficiary_summary"
    assert result.query_contract["filters"]["counterparty"] is None
    assert result.query_contract["filters"]["transaction_type"] == "debit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "language", "placeholder_recipient"),
    [
        ("who I send money give this month", "pcm", "pesin"),
        ("tani mo ran owo si ni osu yi", "yo", "eniyan"),
        ("onye ka m zigara ego n'onwa a", "ig", "nnata"),
        ("wa na tura wa kudi a wannan watan", "ha", "mutanen"),
        ("qui ai je envoye de l argent ce mois ci", "fr", "personnes"),
        ("tani mo send money to this month", "yo", "eniyan"),
    ],
)
async def test_multilingual_recipient_summary_recovery_stays_grouped_and_clears_placeholder_recipient(
    question: str,
    language: str,
    placeholder_recipient: str,
) -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
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
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "beneficiary_summary"
    assert result.query_contract["filters"]["counterparty"] is None
    assert result.query_contract["filters"]["transaction_type"] == "debit"


@pytest.mark.asyncio
async def test_fact_query_shape_does_not_get_upgraded_to_beneficiary_summary_by_locale_recovery() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.DATE,
        filters=QueryFilters(recipient="mum"),
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="when last did I send mum money",
        answer_fact_field="date",
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "when last did I send mum money",
        today=date(2026, 3, 30),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["answer_fact_field"] == "date"
    assert result.query_contract["filters"]["counterparty"] == ["mum"]


@pytest.mark.asyncio
async def test_unscoped_latest_recipient_fact_query_stays_single_transaction() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.COUNTERPARTY,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="who did I send money to last",
        result_reference="latest",
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "who did I send money to last",
        today=date(2026, 3, 30),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["answer_fact_field"] == "counterparty"
    assert result.query_contract["result_reference"] == "latest"
    assert result.query_contract["filters"]["transaction_type"] == "debit"
    assert result.query_contract["filters"]["counterparty"] is None


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
    assert (
        result.resolver_message == "What time period did you mean by 'last'? You can say something like 'last 30 days'."
    )
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
async def test_status_of_last_transaction_deterministically_compiles_to_latest_status_fact() -> None:
    extraction = QueryExtractionResult(intent=ExtractionIntent.TRANSACTION_LIST)
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "What is the status of my last transaction?",
        today=date(2026, 5, 17),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["answer_fact_field"] == "status"
    assert result.query_contract["result_limit"] == 1
    assert result.query_contract["result_reference"] == "latest"


@pytest.mark.asyncio
async def test_typed_latest_received_amount_query_compiles_to_latest_credit_fact_lookup() -> None:
    parser = QueryParser(
        _DummyLLM(
            QueryExtractionResult(
                intent=ExtractionIntent.SINGLE_TRANSACTION,
                request_shape=QueryRequestShape.FACT,
                fact_query_kind=FactQueryKind.AMOUNT,
                answer_fact_field="amount",
                filters=QueryFilters(transaction_type="credit"),
                time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
                raw_query="fallback should not be used",
                result_reference="latest",
            )
        )
    )
    today = date(2026, 3, 21)

    result = await parser.parse(
        "How much did I receive last",
        today=today,
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.resolver_message is None
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_search"
    assert result.query_contract["result_limit"] == 1
    assert result.query_contract["result_reference"] == "latest"
    assert result.query_contract["answer_fact_field"] == "amount"
    assert result.query_contract["time_start"] == today - timedelta(days=180)
    assert result.query_contract["time_end"] == today
    assert result.query_contract["filters"]["transaction_type"] == "credit"


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


@pytest.mark.asyncio
async def test_recent_transaction_list_defaults_to_bounded_30_day_window() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
    )
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "show my recent transactions",
        today=date(2026, 3, 28),
        language="en",
    )

    assert result.outcome == ResolverOutcome.OK
    assert result.pending_clarification is None
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_list"
    assert result.query_contract["time_start"] == date(2026, 2, 26)
    assert result.query_contract["time_end"] == date(2026, 3, 28)


@pytest.mark.asyncio
async def test_day_scoped_singular_transaction_query_normalizes_to_list_query() -> None:
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SINGLE_TRANSACTION,
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
    assert result.query_contract is not None
    assert result.query_contract["intent"] == "transaction_list"
    assert result.query_contract["time_start"] == date(2026, 3, 28)
    assert result.query_contract["time_end"] == date(2026, 3, 28)
    assert result.query_contract["result_limit"] is None
    assert result.query_contract["result_reference"] is None
