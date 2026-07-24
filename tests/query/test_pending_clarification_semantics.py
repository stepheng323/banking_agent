from datetime import date, timedelta

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.models.domain import (
    QueryIntent,
    QueryRequest,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    Ambiguity,
    AmbiguityCode,
    PendingClarificationState,
    QueryExtractionResult,
    QueryParseResult,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.services.reasoning.models import QuerySemanticDecision
from tests.query.factories import make_query_request


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 28)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise NotImplementedError


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _contract(query: QueryRequest) -> QueryRequest:
    return query.model_copy(deep=True)


def _pending_state() -> PendingClarificationState:
    return PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=QueryIntent.ANALYTICS_SUMMARY,
        original_extraction=QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
            ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
            raw_query="How much did I spend last",
        ),
        ambiguities=[Ambiguity(code=AmbiguityCode.TIME_VAGUE, context="last")],
        resolver_message="What time period did you mean by 'last'?",
        language="en",
    )


@pytest.mark.asyncio
async def test_parse_new_query_needs_input_persists_pending_clarification_state() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "How much did I spend last"
        return QueryParseResult(
            outcome=ResolverOutcome.NEEDS_INPUT,
            extraction=pending.original_extraction,
            resolver_message=pending.resolver_message,
            pending_clarification=pending.model_dump(),
            patch={},
        )

    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    result = await step.run({"message": "How much did I spend last", "language": "en", "today": date(2026, 3, 13)})

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert isinstance(result.patch["pending_clarification"], PendingClarificationState)
    assert result.patch["session_active"] is True


@pytest.mark.asyncio
async def test_pending_clarification_time_reply_patches_and_executes() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="clarification_answer",
            confidence=0.99,
            reason="time supplied",
            time_period="last 3 days",
        )

    def _fake_resolve_existing(
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del language
        assert extraction.time_range.days_back == 3
        assert all(ambiguity.code != AmbiguityCode.TIME_VAGUE for ambiguity in extraction.ambiguities)
        contract = _contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=today, end=today),
            )
        )
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=extraction,
            query_request=contract.model_dump(),
            patch={},
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "last 3 days",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "pending_clarification": pending,
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    assert result.patch["pending_clarification"] is None


@pytest.mark.asyncio
async def test_pending_clarification_abort_ends_query_session() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(decision="end_session", confidence=1.0, reason="abort")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "Abort",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "pending_clarification": pending,
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["session_active"] is False
    assert "Transaction cancelled" not in result.response


@pytest.mark.asyncio
async def test_reasoner_fresh_query_without_raw_query_injects_message_for_debit_inference() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)

    updates = await step._parse_reasoner_extraction_to_updates(
        QuerySemanticDecision(
            decision="fresh_query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
            ),
        ),
        state={"message": "How much did I spend this week"},
        today=today,
        language="en",
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.time_start == date(2026, 3, 16)
    assert query_request.time_end == today


@pytest.mark.asyncio
async def test_pending_clarification_new_query_compiles_without_parser_parse() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
                raw_query="How much did I spend this week",
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("pending clarification follow-up should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "How much did I spend this week",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "pending_clarification": pending,
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_pending_clarification_recent_list_interrupts_and_clears_old_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    def _fake_parse_deterministic(question: str, *, today: date, language: str = "en") -> QueryParseResult:
        del language
        assert question == "show my recent transactions"
        contract = _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=today - timedelta(days=29), end=today),
            )
        )
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                raw_query=question,
            ),
            query_request=contract.model_dump(),
            patch={},
        )

    async def _fail_reason(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("fresh query interrupt should bypass pending-clarification reasoner")

    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]
    step.reasoner.reason = _fail_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show my recent transactions",
            "language": "en",
            "today": date(2026, 3, 28),
            "query_session": {
                "session_active": True,
                "pending_clarification": pending,
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    assert result.patch["pending_clarification"] is None


@pytest.mark.asyncio
async def test_pending_clarification_day_scoped_list_interrupts_and_executes_new_query() -> None:
    step = ExtractionStep(_DummyLLM())
    pending = _pending_state()

    def _fake_parse_deterministic(question: str, *, today: date, language: str = "en") -> QueryParseResult:
        del language
        assert question == "show today's transaction"
        contract = _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=today, end=today),
            )
        )
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                raw_query=question,
            ),
            query_request=contract.model_dump(),
            patch={},
        )

    async def _fail_reason(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("day-scoped fresh query should bypass pending-clarification reasoner")

    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]
    step.reasoner.reason = _fail_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show today's transaction",
            "language": "en",
            "today": date(2026, 3, 28),
            "query_session": {
                "session_active": True,
                "pending_clarification": pending,
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
    assert result.patch["pending_clarification"] is None
