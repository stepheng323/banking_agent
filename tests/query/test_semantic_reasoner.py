import json
from datetime import date

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryExecutionContract,
    QueryFrame,
    QueryIR,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    PendingClarificationState,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryParseResult,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.services.reasoning.models import (
    QuerySemanticDecision,
    SemanticReasonerContext,
)
from banking.transactions.query.services.reasoning.reasoner import QuerySemanticReasoner


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 20)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


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
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> QuerySemanticDecision:
        self.prompts.append(prompt)
        self.calls += 1
        return self.decision


class _TrackingLLM:
    def __init__(self, decision: QuerySemanticDecision) -> None:
        self.structured = _TrackingStructured(decision)

    def with_structured_output(self, schema: object) -> _TrackingStructured:
        del schema
        return self.structured


def _direct_answer_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context=context)


def _transaction_list_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.TRANSACTION_LIST, context=context)


def _transaction_surface_item(index: int = 1) -> SurfaceItemView:
    return SurfaceItemView(
        id=f"txn-{index}",
        label=f"Payment {index}",
        amount=1000.0 * index,
        payload=SelectionPayload(
            selection_kind="transaction", entity_type="transaction", entity_id=f"txn-{index}", label=f"Payment {index}"
        ),
        metadata={"bank_name": "Access Bank", "status": "failed"},
    )


def _grouped_summary_surface_view(**context: object) -> SurfaceView:
    return SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context=context)


def _contract(
    query: QueryIR,
) -> QueryExecutionContract:
    assert query.time_range is not None
    return QueryExecutionContract.from_query_ir(query)


def test_reasoner_serializes_focused_surface_contract() -> None:
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Acme Corp",
                    filters_patch={"counterparty": ["Acme Corp"]},
                    fact_capabilities=["date", "reference", "bank"],
                ),
            )
        ],
        context={
            "mode": "direct_answer",
            "focus_type": "beneficiary",
            "selected_item_id": "bene_1",
        },
    )

    payload = json.loads(QuerySemanticReasoner._serialize_surface_snapshot(surface_view=surface_view))

    assert payload["focus_type"] == "beneficiary"
    assert payload["selected_item_id"] == "bene_1"
    assert payload["focused_item"]["selection_kind"] == "beneficiary"
    assert payload["focused_item"]["fact_capabilities"] == ["date", "reference", "bank"]
    assert payload["focused_item"]["has_filters_patch"] is True


def test_reasoner_surface_snapshot_includes_direct_answer_focus_context() -> None:
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        lead_text="That was with Acme Corp.",
        items=[
            SurfaceItemView(
                id="txn-acme",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="txn-acme",
                    label="Acme Corp",
                    fact_capabilities=["date", "amount", "bank", "reference"],
                ),
                metadata={
                    "date": "2026-06-28",
                    "bank_name": "Zenith Bank",
                    "counterparty": "Acme Corp",
                    "type": "credit",
                },
            )
        ],
        context={"type": "single_transaction", "focus_type": "transaction", "selected_item_id": "txn-acme"},
    )

    snapshot = QuerySemanticReasoner._serialize_surface_snapshot(surface_view=surface_view)

    assert "That was with Acme Corp." in snapshot
    assert "2026-06-28" in snapshot
    assert "date" in snapshot
    assert "Acme Corp" in snapshot


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_receipt_action_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "get_receipt"


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_first_item_detail_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[_transaction_surface_item(1), _transaction_surface_item(2)],
        context={"type": "transaction_list"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Show the first one",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "view_details"
    assert decision.drill_down_index == 0
    assert decision.semantic_llm_used is False


@pytest.mark.asyncio
async def test_reasoner_uses_semantic_contract_for_visible_bank_fact() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="semantic_visible_bank_fact",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            drill_down_index=0,
            fact_field="bank",
            requested_field="bank",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[_transaction_surface_item(1)],
        context={"type": "single_transaction"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What bank was that?",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "answer_fact"
    assert decision.fact_field == "bank"
    assert decision.requested_field == "bank"
    assert decision.drill_down_index == 0
    assert decision.semantic_llm_used is True
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_does_not_shortcut_fresh_credit_total_as_focused_amount_fact() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.96,
            reason="semantic_credit_total_query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                raw_query="How much came in this month",
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Acme Corp",
                    filters_patch={"counterparty": ["Acme Corp"]},
                    fact_capabilities=["date", "amount", "bank", "reference"],
                ),
            )
        ],
        context={"focus_type": "beneficiary", "selected_item_id": "bene_1"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="How much came in this month",
            today=date(2026, 6, 27),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 27)),
                    filters=Filters(transaction_type="credit"),
                    aggregation=Aggregation(type="sum", sort_by="amount"),
                    result_limit=1,
                )
            ),
            surface_view=surface_view,
        )
    )

    assert llm.structured.calls == 1
    assert decision.decision == "new_query"
    assert decision.continuation_type is None
    assert decision.drill_down_action is None
    assert decision.fact_field is None


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_show_more_details() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_view_details",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="view_details",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show more details",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "view_details"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_recent_transaction_reset() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show my recent transactions",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_recent_transaction_reset_with_explicit_period() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show my recent transactions in the last 2 weeks",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_new_query_for_day_scoped_singular_list_reset() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="new_query",
            confidence=0.94,
            reason="llm_fresh_list_reset",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="show today's transaction",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "new_query"
    assert decision.reason == "llm_fresh_list_reset"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_llm_scoped_recipient_delta() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_scoped_recipient_delta",
            continuation_type="recipient_drill_down",
            followup_intent="none",
            recipient_name="tolu",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about tolu?",
            today=date(2026, 4, 6),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 4, 1), end=date(2026, 4, 6)),
                    filters=Filters(counterparty=["Mum"], transaction_type="debit"),
                    result_limit=1,
                    result_reference="latest",
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "recipient_drill_down"
    assert decision.reason == "llm_scoped_recipient_delta"
    assert decision.recipient_name == "tolu"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_surface_view_for_deterministic_receipt_action_without_surface_fallbacks() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        context={"type": "single_transaction"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
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
    surface_view = _direct_answer_surface_view(type="single_transaction")
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
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
    surface_view = _grouped_summary_surface_view(type="spending_total")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="That's a lot",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
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
    surface_view = _transaction_list_surface_view(type="transaction_list")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Nice",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "conversational"
    assert decision.response_text == "Anytime. What should we handle next?"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_ends_active_result_session_for_thank_you_with_emoji_without_llm() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.9,
            reason="should_not_be_used",
            continuation_type="conversational",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    surface_view = _grouped_summary_surface_view(type="spending_total")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Thank you 😊",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
        )
    )

    assert decision.decision == "end_session"
    assert decision.reason == "deterministic_end_session"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_beneficiary_summary_followup() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_beneficiary_fact_followup",
            continuation_type="recipient_drill_down",
            recipient_name="Adesanya Kunle",
            fact_field="date",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    item = QueryResultItem(
        id="bene_1",
        description="Adesanya Kunle",
        amount=50000.0,
        date=date(2026, 3, 27),
        metadata={"recipient_name": "Adesanya Kunle"},
    )
    surface_view = SurfaceView(
        mode=SurfaceViewMode.GROUPED_SUMMARY,
        items=[
            SurfaceItemView(
                id="bene_1",
                label="Adesanya Kunle",
                amount=50000.0,
                count=1,
                payload=SelectionPayload(
                    selection_kind="beneficiary",
                    entity_type="beneficiary",
                    entity_id="bene_1",
                    label="Adesanya Kunle",
                ),
            )
        ],
        context={"view": "beneficiary_summary"},
    )

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="When was Kunle's transaction?",
            today=date(2026, 3, 28),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 14), end=date(2026, 3, 28)),
                )
            ),
            items=[item],
            surface_view=surface_view,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "recipient_drill_down"
    assert decision.recipient_name == "Adesanya Kunle"
    assert decision.fact_field == "date"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_deterministic_surface_action_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface_view = _direct_answer_surface_view(type="single_transaction")

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
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

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

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
    surface_view = _direct_answer_surface_view(type="single_transaction")
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
            surface_view=surface_view,
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
    query_trace_events = [payload for event, payload in events if event == "query_trace"]
    assert query_trace_events
    assert query_trace_events[0]["query_phase"] == "semantic_reasoner"
    assert query_trace_events[0]["llm_used"] is True
    assert query_trace_events[0]["prompt_item_count"] == 1


@pytest.mark.asyncio
async def test_reasoner_logs_llm_backed_fresh_query_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.93,
            reason="llm_fresh_query",
            extraction=QueryExtractionResult(raw_query="can you show my last transaction", result_limit=1),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="can you show my last transaction",
            today=date(2026, 3, 13),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
        )
    )

    assert llm.structured.calls == 1
    assert decision.decision == "fresh_query"
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "fresh_query",
            "reasoner_context_mode": "active_result",
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 2, 17), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 16)
    assert decision.time_range.end == date(2026, 3, 19)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_last_week_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_last_week_contrastive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about the week prior",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit", merchant=["mum"]),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is None
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_today_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_replace_scope_today",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="fetch today only",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 19)
    assert decision.time_range.end == date(2026, 3, 19)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_yesterday_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_replace_scope_yesterday_contrastive",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 18), end=date(2026, 3, 18), granularity="day"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="what about the day before",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 3, 18)
    assert decision.time_range.end == date(2026, 3, 18)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_single_item_contrastive_yesterday_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.92,
            reason="llm_single_item_replace_scope_yesterday",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="no transaction the day prior?",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 17), end=date(2026, 3, 19)),
                    result_limit=1,
                    result_reference="latest",
                )
            ),
            surface_view=_direct_answer_surface_view(type="single_transaction"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.extraction is not None
    assert decision.extraction.time_range is not None
    assert decision.extraction.time_range.period == "yesterday"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_contrastive_last_week_replace_scope_with_extraction() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_replace_scope_last_week_contrastive_extraction",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="What about the week before",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit", merchant=["mum"]),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.extraction is not None
    assert decision.extraction.time_range is not None
    assert decision.extraction.time_range.period == "last_week"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_continue_pagination_without_llm() -> None:
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "show_more"
    assert decision.followup_intent == "continue_pagination"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_previous_pagination_without_llm() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_previous_pagination",
            continuation_type="show_more",
            followup_intent="previous_pagination",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="previous page",
            today=date(2026, 3, 14),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "show_more"
    assert decision.followup_intent == "previous_pagination"
    assert llm.structured.calls == 0


@pytest.mark.asyncio
async def test_reasoner_passes_through_show_evidence_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.94,
            reason="llm_show_aggregate_evidence",
            continuation_type="show_evidence",
            followup_intent="refine_existing",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="Show me",
            today=date(2026, 3, 14),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                    filters=Filters(transaction_type="debit"),
                    aggregation=Aggregation(type="sum"),
                )
            ),
            surface_view=_grouped_summary_surface_view(view="summary"),
        )
    )

    assert decision.continuation_type == "show_evidence"
    assert decision.followup_intent == "refine_existing"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_grouped_total_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_grouped_total_followup",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="so what the total?",
            today=date(2026, 3, 30),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.BENEFICIARY_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 30)),
                    filters=Filters(transaction_type="debit"),
                    aggregation=Aggregation(type="sum", sort_by="count"),
                )
            ),
            surface_view=_grouped_summary_surface_view(view="summary"),
        )
    )

    assert decision.continuation_type == "grouped_total_followup"
    assert decision.followup_intent == "refine_existing"
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
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_grouped_summary_surface_view(type="spending_total"),
        )
    )

    assert decision.continuation_type == "unclear"
    assert decision.followup_intent == "none"
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_passes_through_last_month_replace_scope_followup_intent() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            reason="llm_replace_scope_last_month",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month"),
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="for last month only",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 1, 1), end=date(2026, 3, 19)),
                    filters=Filters(transaction_type="debit"),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    assert decision.continuation_type == "time_delta"
    assert decision.followup_intent == "replace_scope"
    assert decision.time_range is not None
    assert decision.time_range.start == date(2026, 2, 1)
    assert decision.time_range.end == date(2026, 2, 28)
    assert llm.structured.calls == 1


@pytest.mark.asyncio
async def test_reasoner_logs_single_llm_trace_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.91,
            reason="llm_replace_scope",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_period="last week",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    await reasoner.reason(
        SemanticReasonerContext(
            message="can we compare that against last week",
            today=date(2026, 3, 14),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 14)),
                )
            ),
            surface_view=_transaction_list_surface_view(type="transaction_list"),
        )
    )

    query_trace_events = [payload for event, payload in events if event == "query_trace"]
    assert query_trace_events
    assert query_trace_events[0]["reasoner_schema"] == "active_continuation"
    assert query_trace_events[0]["llm_calls_used"] == 1
    assert query_trace_events[0]["single_llm_invariant"] is True
    assert isinstance(query_trace_events[0]["context_bytes"], int)
    assert query_trace_events[0]["context_bytes"] > 0
    llm_call_events = [payload for event, payload in events if event == "query_reasoner_llm_call"]
    assert llm_call_events
    assert llm_call_events[0]["reasoner_schema"] == "active_continuation"
    assert llm_call_events[0]["prompt_surface_type"] == "list"
    assert isinstance(llm_call_events[0]["context_bytes"], int)
    assert llm_call_events[0]["context_bytes"] > 0


@pytest.mark.asyncio
async def test_reasoner_uses_llm_for_pending_clarification_time_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, dict(kwargs)))

    monkeypatch.setattr("banking.transactions.query.services.reasoning.reasoner.logger.info", _capture)

    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="clarification_answer",
            confidence=0.9,
            reason="llm_pending_time_reply",
            time_period="last 3 days",
        )
    )
    reasoner = QuerySemanticReasoner(llm)
    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="last 3 days",
            today=date(2026, 3, 13),
            language="en",
            pending_clarification=PendingClarificationState(
                original_query="How much did I spend last",
                current_intent=QueryIntent.ANALYTICS_SUMMARY,
                original_extraction=QueryExtractionResult(raw_query="How much did I spend last"),
                resolver_message="What time period did you mean by 'last'?",
                language="en",
            ),
        )
    )

    assert decision.decision == "clarification_answer"
    assert llm.structured.calls == 1
    assert (
        "query_reasoner_decision",
        {
            "reasoner_decision": "clarification_answer",
            "reasoner_context_mode": "pending_clarification",
            "reasoner_llm_used": True,
            "continuation_type": None,
            "confidence": 0.9,
            "reason": "llm_pending_time_reply",
        },
    ) in events


@pytest.mark.asyncio
async def test_reasoner_bounds_prompt_items_and_frames() -> None:
    llm = _TrackingLLM(
        QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_bounded_prompt",
            continuation_type="time_delta",
            followup_intent="replace_scope",
        )
    )
    reasoner = QuerySemanticReasoner(llm)

    items = [
        QueryResultItem(
            id=f"txn-{index}",
            description=f"Payment {index}",
            amount=1000.0 + index,
            date=date(2026, 3, 13),
            metadata={"status": "success", "bank_name": "Zenith"},
        )
        for index in range(6)
    ]
    query_frames = [
        QueryFrame(
            frame_id=f"qf_{index}",
            turn_index=index + 1,
            summary_text=f"summary {index}",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.TRANSACTION_LIST,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                )
            ),
        )
        for index in range(5)
    ]

    await reasoner.reason(
        SemanticReasonerContext(
            message="what about the week before",
            today=date(2026, 3, 19),
            language="en",
            query_contract=_contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19)),
                )
            ),
            items=items,
            surface_view=_transaction_list_surface_view(type="transaction_list", count=5),
            query_frames=query_frames,
        )
    )

    assert llm.structured.calls == 1
    assert llm.structured.prompts
    prompt_str = str(llm.structured.prompts[0])
    assert "Payment 0" in prompt_str
    assert "Payment 4" in prompt_str
    assert "Payment 5" not in prompt_str
    assert "summary 4" in prompt_str
    assert "summary 2" in prompt_str
    assert "summary 1" not in prompt_str


@pytest.mark.asyncio
async def test_extraction_step_preserves_session_for_conversational_reaction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 14)),
        )
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
                "query_result": {
                    "summary_text": "You spent money in that period.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(type="spending_total").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["response"] == "It is on the high side.\n\nYou can ask what made it up."
    assert result.patch["session_active"] is False
    assert result.patch["flow_state"] == "complete"
    assert result.patch["_query_session_transition"] == "exit_query_session_conversational"


@pytest.mark.asyncio
async def test_extraction_step_fresh_query_uses_deterministic_parser_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())
    parsed_result = QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=QueryExtractionResult(
            raw_query="show my last transaction",
            result_limit=1,
            result_reference="latest",
        ),
        query_contract=_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 2, 11), end=date(2026, 3, 13)),
                result_limit=1,
                result_reference="latest",
            )
        ).model_dump(mode="json"),
    )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("fresh query should not call parser.parse")

    step.parser.parse_deterministic = lambda *args, **kwargs: parsed_result  # type: ignore[method-assign]
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
async def test_extraction_step_fresh_query_falls_back_to_parser_parse_when_not_deterministic() -> None:
    step = ExtractionStep(_FailingLLM())
    parsed_extraction = QueryExtractionResult(
        raw_query="show my last transaction",
        result_limit=1,
        result_reference="latest",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 2, 11), end=date(2026, 3, 13)),
        result_limit=1,
        result_reference="latest",
    )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "show my last transaction"
        return QueryParseResult(
            outcome=ResolverOutcome.OK,
            extraction=parsed_extraction,
            query_contract=_contract(parsed_query).model_dump(mode="json"),
        )

    step.parser.parse_deterministic = lambda *args, **kwargs: None  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

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
async def test_extraction_step_does_not_reparse_support_problem_as_query_continuation() -> None:
    step = ExtractionStep(_FailingLLM())
    today = date(2026, 3, 13)
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=today),
        )
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.95,
            reason="misread support issue as query",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                raw_query="I was debited but they didn't receive it",
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today"),
            ),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {
            "message": "I was debited but they didn't receive it",
            "language": "en",
            "today": today,
        },
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == "I'm not sure what you're referring to. Could you rephrase?"
    assert "query_contract" not in updates


@pytest.mark.asyncio
async def test_extraction_step_active_result_aggregate_can_reuse_reasoner_extraction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
            filters=Filters(transaction_type="debit"),
        )
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
                "query_result": {
                    "summary_text": "Transactions",
                    "items": [QueryResultItem(description="Txn", amount=1000, date=date(2026, 3, 13)).model_dump()],
                    "surface_view": _transaction_list_surface_view().model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_active_result_new_query_compiles_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
        )
    )

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
        raise AssertionError("active-session new_query should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "How much did I spend this week",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_contract": session_contract,
                "query_result": {
                    "summary_text": "You spent money in that period.",
                    "items": [],
                    "surface_view": _grouped_summary_surface_view(type="spending_total").model_dump(mode="json"),
                },
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
