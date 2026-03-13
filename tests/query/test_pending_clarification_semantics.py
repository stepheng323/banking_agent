from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    Ambiguity,
    AmbiguityCode,
    ExtractionIntent,
    NormalizedQuery,
    PendingClarificationState,
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
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise NotImplementedError


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _pending_state() -> PendingClarificationState:
    return PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=ExtractionIntent.SPENDING_TOTAL,
        original_extraction=QueryExtractionResult(
            intent=ExtractionIntent.SPENDING_TOTAL,
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

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            extraction=pending.original_extraction,
        )

    def _fake_resolve_existing(
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert extraction.raw_query == "How much did I spend last"
        return QueryParseResult(
            outcome=ResolverOutcome.NEEDS_INPUT,
            extraction=extraction,
            resolver_message=pending.resolver_message,
            pending_clarification=pending.model_dump(),
            patch={},
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

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
        contract = QueryExecutionContract(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_start=today,
            time_end=today,
            filters=None,
            normalized_query=NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=today, end=today),
            ),
        )
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=extraction,
            query_contract=contract.model_dump(),
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
