from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import (
    QuerySemanticDecision,
    QuerySemanticReasoner,
    SemanticReasonerContext,
)
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


class _FailingStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise AssertionError("LLM should not be called")


class _FailingLLM:
    def with_structured_output(self, schema: object) -> _FailingStructured:
        del schema
        return _FailingStructured()


class _TrackingStructured:
    def __init__(self, decision: QuerySemanticDecision) -> None:
        self.decision = decision
        self.calls = 0

    async def ainvoke(self, prompt: str) -> QuerySemanticDecision:
        del prompt
        self.calls += 1
        return self.decision


class _TrackingLLM:
    def __init__(self, decision: QuerySemanticDecision) -> None:
        self.structured = _TrackingStructured(decision)

    def with_structured_output(self, schema: object) -> _TrackingStructured:
        del schema
        return self.structured


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_receipt_action_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "get_receipt"


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_fact_answer() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_fact_status",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="answer_fact",
            fact_field="status",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})
    item = QueryResultItem(
        id="txn-1",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"status": "processing", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="was it successful?",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
            items=[item],
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "answer_fact"
    assert decision.fact_field == "status"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_conversational_reaction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="llm_conversational_reaction",
            continuation_type="conversational",
            response_text="It is on the high side.",
            contextual_hint="You can ask what made it up.",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"type": "spending_total"})

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="That's a lot",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_start=date(2026, 3, 9),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "conversational"
    assert decision.response_text == "It is on the high side."
    assert decision.contextual_hint == "You can ask what made it up."
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_active_result_appreciation_reaction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="llm_appreciation_reaction",
            continuation_type="conversational",
            response_text="Anytime. What should we handle next?",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface = ResultSurface(type=SurfaceType.LIST, items=[], context={"type": "transaction_list"})

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Nice",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_LIST,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "conversational"
    assert decision.response_text == "Anytime. What should we handle next?"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_deterministic_surface_action_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.services.reasoner.logger.info", _capture)

    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
        )
    )

    assert decision.drill_down_action == "get_receipt"
    assert (
        "query_surface_action_deterministic",
        {"reasoner_context_mode": "active_result", "action": "get_receipt", "reason": "deterministic_receipt"},
    ) in events
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "continuation",
            "reasoner_context_mode": "active_result",
            "reasoner_llm_used": False,
            "continuation_type": "drill_down",
            "confidence": 1.0,
            "reason": "deterministic_receipt",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_logs_llm_fact_answer_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.services.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_fact_recipient",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="answer_fact",
            fact_field="recipient",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})
    item = QueryResultItem(
        id="txn-1",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"status": "processing", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="who was it to?",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
            items=[item],
        )
    )

    assert decision.fact_field == "recipient"
    assert llm.structured.calls == 1
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "continuation",
            "reasoner_context_mode": "active_result",
            "reasoner_llm_used": True,
            "continuation_type": "drill_down",
            "confidence": 0.91,
            "reason": "llm_fact_recipient",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_logs_llm_backed_fresh_query_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.services.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.93,
            reason="llm_fresh_query",
            extraction=QueryExtractionResult(raw_query="show my last transaction", result_limit=1),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show my last transaction",
            today=date(2026, 3, 13),
            language="en",
        )
    )

    assert llm.structured.calls == 1
    assert decision.decision == "fresh_query"
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "fresh_query",
            "reasoner_context_mode": "none",
            "reasoner_llm_used": True,
            "continuation_type": None,
            "confidence": 0.93,
            "reason": "llm_fresh_query",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_passes_through_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.92,
            reason="llm_replace_scope",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last week only",
            today=date(2026, 3, 14),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_LIST,
                time_start=date(2026, 3, 1),
                time_end=date(2026, 3, 14),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                ),
            ),
            surface=ResultSurface(type=SurfaceType.LIST, items=[], context={"type": "transaction_list"}),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_possessive_week_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_this_week_possessive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Only this week's",
            today=date(2026, 3, 19),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_LIST,
                time_start=date(2026, 2, 17),
                time_end=date(2026, 3, 19),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 2, 17), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                ),
            ),
            surface=ResultSurface(type=SurfaceType.LIST, items=[], context={"type": "transaction_list"}),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 16)
    assert decision.time_range.end == date(2026, 3, 19)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_continue_pagination_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_continue_pagination",
            continuation_type="show_more",
            followup_intent="continue_pagination",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="next page",
            today=date(2026, 3, 14),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_LIST,
                time_start=date(2026, 3, 8),
                time_end=date(2026, 3, 14),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14)),
                ),
            ),
            surface=ResultSurface(type=SurfaceType.LIST, items=[], context={"type": "transaction_list"}),
        )
    )

    assert decision.continuation_type == "show_more"
    assert decision.followup_intent == "continue_pagination"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_unclear_followup_contract() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.31,
            reason="ambiguous_followup",
            continuation_type="unclear",
            followup_intent="none",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last week only",
            today=date(2026, 3, 14),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_start=date(2026, 3, 1),
                time_end=date(2026, 3, 14),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                ),
            ),
            surface=ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"type": "spending_total"}),
        )
    )

    assert decision.continuation_type == "unclear"
    assert decision.followup_intent == "none"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_deterministic_pending_clarification_answer_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.services.reasoner.logger.info", _capture)

    reasoner = QuerySemanticReasoner(_FailingLLM())
    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="last 3 days",
            today=date(2026, 3, 13),
            language="en",
            pending_clarification=PendingClarificationState(
                original_query="How much did I spend last",
                current_intent=ExtractionIntent.SPENDING_TOTAL,
                original_extraction=QueryExtractionResult(raw_query="How much did I spend last"),
                resolver_message="What time period did you mean by 'last'?",
                language="en",
            ),
        )
    )

    assert decision.decision == "clarification_answer"
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "clarification_answer",
            "reasoner_context_mode": "pending_clarification",
            "reasoner_llm_used": False,
            "continuation_type": None,
            "confidence": 0.99,
            "reason": "deterministic_time_reply",
        },
    ) in events


@pytest.mark.asyncio
async def test_extraction_step_preserves_session_for_conversational_reaction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = QueryExecutionContract(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_start=date(2026, 3, 9),
        time_end=date(2026, 3, 14),
        normalized_query=NormalizedQuery(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 14)),
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="conversational",
            response_text="It is on the high side.",
            contextual_hint="You can ask what made it up.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "That's a lot",
            "language": "en",
            "today": date(2026, 3, 14),
            "query_session": {
                "session_active": True,
                "query_contract": session_contract,
                "query_result": {"items": []},
                "surface": ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"type": "spending_total"}),
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["response"] == "It is on the high side.\n\nYou can ask what made it up."
    assert result.patch["session_active"] is False
    assert result.patch["flow_state"] == "complete"
    assert result.patch["_query_session_transition"] == "exit_query_session_conversational"


@pytest.mark.asyncio
async def test_extraction_step_fresh_query_uses_reasoner_extraction_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            extraction=QueryExtractionResult(
                raw_query="show my last transaction",
                result_limit=1,
                result_reference="latest",
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("fresh query should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show my last transaction",
            "language": "en",
            "today": date(2026, 3, 13),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_active_result_aggregate_can_reuse_reasoner_extraction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = QueryExecutionContract(
        intent=QueryIntent.TRANSACTION_LIST,
        time_start=date(2026, 3, 1),
        time_end=date(2026, 3, 13),
        normalized_query=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
            filters=Filters(transaction_type="debit"),
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            extraction=QueryExtractionResult(
                raw_query="how much did i spend",
                result_limit=None,
                filters=QueryFilters(transaction_type="debit"),
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("aggregate continuation should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "how much did i spend",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_contract": session_contract,
                "query_result": {"items": [QueryResultItem(description="Txn", amount=1000, date=date(2026, 3, 13)).model_dump()]},
                "surface": ResultSurface(type=SurfaceType.LIST, items=[], context={}),
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
