from datetime import date, timedelta
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.actions import handle_drill_down
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ExtractionIntent,
    Filters,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryIR,
    QueryOperation,
    QueryParseResult,
    QueryResult,
    QueryResultItem,
    QueryTimeRange,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticDecision
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.core.src.agent.shared.query_contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from shared.i18n import render_message


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 4)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return QueryExtractionResult(raw_query="fallback")


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _contract(
    query: QueryIR,
    *,
    comparison: Any | None = None,
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
) -> QueryExecutionContract:
    assert query.time_range is not None
    if comparison is None and continuation_type is None and continuation_delta_type is None:
        return QueryExecutionContract.from_query_ir(query)
    return QueryExecutionContract.from_query_ir(
        query.model_copy(
            update={
                "comparison": comparison.model_copy(deep=True) if comparison is not None else None,
                "continuation_type": continuation_type,
                "continuation_delta_type": continuation_delta_type,
            }
        )
    )


def _ok_result(extraction: QueryExtractionResult, query: QueryIR) -> QueryParseResult:
    contract = _contract(query)
    return QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=extraction,
        query_contract=contract.model_dump(mode="json"),
    )


@pytest.mark.asyncio
async def test_parse_new_query_does_not_inherit_time_range_for_unspecified_time() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 4)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show my transfers",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=30), end=today),
    )
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.95,
            reason="fresh_unspecified_query",
            extraction=extraction,
        )

    def _fake_resolve_existing(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    updates = await step._parse_new_query(
        {
            "message": "show my transfers",
            "today": today,
            "language": "en",
            "query_session": {
                "session_active": True,
                "query_contract": session_contract.model_dump(),
            },
        }
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == today - timedelta(days=30)
    assert query_contract.time_end == today


@pytest.mark.asyncio
async def test_recipient_ranking_followup_reparses_as_new_beneficiary_summary_query() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 7)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
    )
    session_contract = _contract(session_query)

    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
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
        query = _query_ir(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 2), end=date(2026, 3, 7)),
        )
        return _ok_result(parsed_extraction, query)

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
        {"message": "who did I send money to the most this week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.time_start == date(2026, 3, 2)
    assert query_contract.time_end == today


@pytest.mark.asyncio
async def test_direct_answer_show_more_details_stays_anchored_to_selected_transaction() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(counterparty=["Mum"], transaction_type="debit"),
        answer_fact_field="date",
    )
    session_contract = _contract(session_query)

    older_item = QueryResultItem(
        id="txn-older",
        description="Mum",
        amount=50000,
        date=date(2026, 3, 25),
        metadata={"type": "debit", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )
    selected_item = QueryResultItem(
        id="txn-selected",
        description="Mum",
        amount=50000,
        date=date(2026, 3, 28),
        metadata={"type": "debit", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )
    selected_payload = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id="txn-selected",
        label="Mum",
        fact_capabilities=["date", "amount", "bank", "counterparty"],
    )
    query_result = QueryResult(
        summary_text="That transaction was on March 28, 2026.",
        items=[older_item, selected_item],
        query_contract=session_contract,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id="txn-selected",
                    label="Mum",
                    amount=50000,
                    payload=selected_payload,
                    metadata=selected_item.metadata or {},
                )
            ],
            context={"type": "single_transaction", "selected_item_id": "txn-selected"},
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            confidence=0.99,
            reason="deterministic_view_details",
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action="view_details",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show more details", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    assert updates["drill_down_action"] == "view_details"
    selected_payload = updates["selected_payload"]
    assert selected_payload.entity_id == "txn-selected"

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            **updates,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert "March 28, 2026" in (result.response or "")
    assert "March 25, 2026" not in (result.response or "")


@pytest.mark.asyncio
async def test_fresh_recent_transactions_followup_replaces_scope_instead_of_inheriting_counterparty() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(counterparty=["Mum"], transaction_type="debit"),
        answer_fact_field="date",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.97,
            reason="deterministic_fresh_list_reset",
        )

    def _fake_parse_deterministic(
        question: str,
        *,
        today: date,
        language: str = "en",
    ) -> QueryParseResult:
        del language
        assert question == "show my recent transactions"
        parsed_query = _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=today - timedelta(days=30), end=today),
        )
        return _ok_result(
            QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                raw_query=question,
            ),
            parsed_query,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show my recent transactions", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": QueryResult(
                summary_text="That transaction was on March 28, 2026.",
                items=[],
                query_contract=session_contract,
                surface_view=SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context={"type": "single_transaction"}),
            ).model_dump(mode="json"),
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.filters is None or query_contract.filters.counterparty is None
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_fresh_recent_transactions_with_explicit_period_replaces_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(counterparty=["Mum"], transaction_type="debit"),
        answer_fact_field="date",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.97,
            reason="deterministic_fresh_list_reset",
        )

    def _fake_parse_deterministic(
        question: str,
        *,
        today: date,
        language: str = "en",
    ) -> QueryParseResult | None:
        del today, language
        assert question == "show my recent transactions in the last 2 weeks"
        return None

    async def _fake_parse(
        question: str,
        *,
        today: date,
        language: str = "en",
    ) -> QueryParseResult:
        del language
        assert question == "show my recent transactions in the last 2 weeks"
        parsed_query = _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=today - timedelta(days=14), end=today),
        )
        return _ok_result(
            QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                raw_query=question,
            ),
            parsed_query,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show my recent transactions in the last 2 weeks", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": QueryResult(
                summary_text="That transaction was on March 28, 2026.",
                items=[],
                query_contract=session_contract,
                surface_view=SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context={"type": "single_transaction"}),
            ).model_dump(mode="json"),
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.filters is None or query_contract.filters.counterparty is None
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_replace_scope_resets_pagination_and_preserves_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(merchant=["Mum"], transaction_type="debit"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
            delta_type="time",
            confidence=0.96,
            reason="llm_replace_scope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 8)
    assert query_contract.time_end == date(2026, 3, 14)
    assert query_contract.filters is not None
    assert query_contract.filters.merchant == ["Mum"]
    assert query_contract.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_continue_pagination_only_advances_page_without_scope_mutation() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="continue_pagination",
            confidence=0.97,
            reason="llm_continue_pagination",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "more", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["current_page"] == 2
    assert "query_contract" not in updates


@pytest.mark.asyncio
async def test_invalid_time_delta_and_continue_pagination_combo_requests_clarification() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="continue_pagination",
            time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
            delta_type="time",
            confidence=0.9,
            reason="invalid_pagination_time_combo",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_recipient_drilldown_follow_up_applies_counterparty_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 10)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="recipient_drill_down",
            followup_intent="none",
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
    assert updates["query_contract"].filters is not None
    assert updates["query_contract"].filters.counterparty == ["Gaines"]


@pytest.mark.asyncio
async def test_recipient_fact_drilldown_follow_up_converts_summary_to_transaction_list_with_fact_answer() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 27)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 14), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="recipient_drill_down",
            followup_intent="none",
            recipient_name="Adesanya Kunle",
            fact_field="date",
            delta_type="filter",
            confidence=0.99,
            reason="deterministic_recipient_fact_drill_down",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was Kunle's transaction?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_contract = updates["query_contract"]
    assert updates["flow_state"] == "executing"
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.aggregation is None
    assert query_contract.answer_fact_field == "date"
    assert query_contract.filters is not None
    assert query_contract.filters.counterparty == ["Adesanya Kunle"]


@pytest.mark.asyncio
async def test_show_me_follow_up_converts_summary_to_transactions_when_explicitly_requested() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="refine_existing",
            confidence=1.0,
            reason="llm_show_underlying_transactions",
            delta_type="none",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["query_contract"].intent == QueryIntent.TRANSACTION_LIST
    assert updates["query_contract"].time_start == date(2026, 3, 1)
    assert updates["query_contract"].time_end == today
    assert updates["query_contract"].answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["resolver_message"] is None


@pytest.mark.asyncio
async def test_show_evidence_follow_up_converts_aggregate_summary_to_scoped_transactions_and_clears_fact_anchor() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum"),
        answer_fact_field="date",
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_evidence",
            followup_intent="refine_existing",
            confidence=0.96,
            reason="llm_show_aggregate_evidence",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You spent ₦50,000 this month so far.",
                "items": [
                    QueryResultItem(
                        id="txn_001",
                        description="Transfer to Mum",
                        amount=50000,
                        date=date(2026, 3, 26),
                        metadata={"type": "debit", "bank_name": "Zenith Bank"},
                    ).model_dump(mode="json")
                ],
            },
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.aggregation is None
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.answer_fact_field is None
    assert query_contract.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_explain_aggregate_scope_follow_up_uses_scoped_reply_without_drill_down() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="explain_aggregate_scope",
            followup_intent="none",
            confidence=0.93,
            reason="llm_explain_aggregate_scope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How all this take be 50k", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You spent ₦50,000 from Mar 01 to Mar 29, across 1 transaction.",
                "items": [
                    QueryResultItem(
                        id="txn_001",
                        description="Transfer to Mum",
                        amount=50000,
                        date=date(2026, 3, 26),
                        metadata={"type": "debit", "bank_name": "Zenith Bank"},
                    ).model_dump(mode="json")
                ],
            },
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert "Debit Transactions" in updates["response"]
    assert "₦50,000" in updates["response"]
    assert updates["session_active"] is True
    assert updates["flow_state"] == "complete"


@pytest.mark.asyncio
async def test_account_breakdown_drilldown_converts_to_transaction_list_with_account_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        query_operation=QueryOperation.BREAKDOWN_TRANSACTIONS,
        time_range=TimeRange(start=date(2026, 2, 26), end=today),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_index=1,
            confidence=0.99,
            reason="account_breakdown_drill_down",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show all for first bank", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "Spending by account",
                "items": [
                    QueryResultItem(
                        id="0",
                        description="Zenith Bank",
                        amount=-1099852,
                        date=today,
                        metadata={"key": "Zenith Bank", "count": 23},
                    ).model_dump(mode="json"),
                    QueryResultItem(
                        id="1",
                        description="First Bank",
                        amount=-96200,
                        date=today,
                        metadata={"key": "First Bank", "count": 5},
                    ).model_dump(mode="json"),
                ],
                "surface_view": {
                    "mode": "grouped_summary",
                    "context": {"group_by": "account"},
                    "items": [
                        {
                            "id": "0",
                            "label": "Zenith Bank",
                            "amount": -1099852,
                            "count": 23,
                            "payload": {
                                "selection_kind": "group_bucket",
                                "entity_type": "group_bucket",
                                "entity_id": "0",
                                "label": "Zenith Bank",
                                "group_by": "account",
                                "group_key": "Zenith Bank",
                                "filters_patch": {"account_filter": "Zenith Bank"},
                            },
                        },
                        {
                            "id": "1",
                            "label": "First Bank",
                            "amount": -96200,
                            "count": 5,
                            "payload": {
                                "selection_kind": "group_bucket",
                                "entity_type": "group_bucket",
                                "entity_id": "1",
                                "label": "First Bank",
                                "group_by": "account",
                                "group_key": "First Bank",
                                "filters_patch": {"account_filter": "First Bank"},
                            },
                        },
                    ],
                },
            },
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.query_operation == QueryOperation.LIST_TRANSACTIONS
    assert query_contract.aggregation is None
    assert query_contract.filters is not None
    assert query_contract.filters.account_filter == "First Bank"
    assert query_contract.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_account_breakdown_drilldown_prefers_explicit_label_over_ordinal_index_hint() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        query_operation=QueryOperation.BREAKDOWN_TRANSACTIONS,
        time_range=TimeRange(start=date(2026, 2, 26), end=today),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_index=0,
            confidence=0.99,
            reason="account_breakdown_drill_down",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show the first bank transactions", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "Spending by account",
                "items": [
                    QueryResultItem(
                        id="0",
                        description="Zenith Bank",
                        amount=-1099852,
                        date=today,
                        metadata={"key": "Zenith Bank", "count": 23},
                    ).model_dump(mode="json"),
                    QueryResultItem(
                        id="1",
                        description="First Bank",
                        amount=-96200,
                        date=today,
                        metadata={"key": "First Bank", "count": 5},
                    ).model_dump(mode="json"),
                ],
                "surface_view": {
                    "mode": "grouped_summary",
                    "context": {"group_by": "account"},
                    "items": [
                        {
                            "id": "0",
                            "label": "Zenith Bank",
                            "amount": -1099852,
                            "count": 23,
                            "payload": {
                                "selection_kind": "group_bucket",
                                "entity_type": "group_bucket",
                                "entity_id": "0",
                                "label": "Zenith Bank",
                                "group_by": "account",
                                "group_key": "Zenith Bank",
                                "filters_patch": {"account_filter": "Zenith Bank"},
                            },
                        },
                        {
                            "id": "1",
                            "label": "First Bank",
                            "amount": -96200,
                            "count": 5,
                            "payload": {
                                "selection_kind": "group_bucket",
                                "entity_type": "group_bucket",
                                "entity_id": "1",
                                "label": "First Bank",
                                "group_by": "account",
                                "group_key": "First Bank",
                                "filters_patch": {"account_filter": "First Bank"},
                            },
                        },
                    ],
                },
            },
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.filters is not None
    assert query_contract.filters.account_filter == "First Bank"


@pytest.mark.asyncio
async def test_show_me_follow_up_does_not_convert_summary_on_pagination_intent() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="continue_pagination",
            confidence=0.95,
            reason="pagination_on_summary_is_invalid",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_weekly_summary_show_them_then_only_this_weeks_replaces_scope_and_resets_pagination() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 2, 17), end=today),
        filters=Filters(transaction_type="debit"),
    )
    session_contract = _contract(session_query)

    decisions = iter(
        [
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="show_more",
                followup_intent="refine_existing",
                confidence=0.99,
                reason="llm_show_underlying_transactions",
            ),
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="time_delta",
                followup_intent="replace_scope",
                time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
                delta_type="time",
                confidence=0.97,
                reason="llm_replace_scope_this_week_possessive",
            ),
        ]
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return next(decisions)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    first_updates = await step._handle_continuation(
        {"message": "Show them", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert first_updates["query_contract"].intent == QueryIntent.TRANSACTION_LIST
    assert first_updates["query_contract"].time_start == date(2026, 2, 17)
    assert first_updates["query_contract"].time_end == today
    assert first_updates["current_page"] == 0

    second_updates = await step._handle_continuation(
        {"message": "Only this week's", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": first_updates["query_contract"].model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = second_updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.time_start == date(2026, 3, 16)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert second_updates["current_page"] == 0
    assert second_updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_replaces_scope_and_preserves_recipient_and_debit_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.97,
            reason="llm_replace_scope_last_week",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 9)
    assert query_contract.time_end == date(2026, 3, 15)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_logs_semantic_reasoner_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.97,
            reason="llm_replace_scope_last_week",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert (
        "query_continuation_resolution",
        {
            "path": "semantic_reasoner",
            "semantic_decision": "continuation",
            "continuation_type": "time_delta",
            "followup_intent": "replace_scope",
            "delta_type": None,
        },
    ) in events


@pytest.mark.asyncio
async def test_low_confidence_unclear_last_week_recovers_via_time_rescope_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 9)
    assert query_contract.time_end == date(2026, 3, 15)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert (
        "query_continuation_resolution",
        {
            "path": "time_rescope_recovery",
            "trigger_reason": "low_confidence_unclear",
            "recovered": True,
            "session_has_query_contract": True,
            "resolved_time_range": True,
            "resolved_time_start": "2026-03-09",
            "resolved_time_end": "2026-03-15",
            "preserved_query_shape": True,
            "skip_reason": None,
        },
    ) in events


@pytest.mark.asyncio
async def test_grounded_ask_clarify_last_week_recovers_via_time_rescope_recovery() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            answer_mode="ask_clarify",
            confidence=0.84,
            reason="grounded_reference_ambiguous",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 9)
    assert query_contract.time_end == date(2026, 3, 15)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_aggregate_followup_without_extraction_preserves_active_query_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.93,
            reason="llm_total_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much total", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert query_contract.aggregation is not None
    assert query_contract.aggregation.type == "sum"
    assert query_contract.answer_fact_field is None
    assert query_contract.result_limit is None
    assert query_contract.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_explicit_aggregate_scope_drops_inherited_beneficiary_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 2, 27), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.SPENDING_TOTAL,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
                raw_query="How have I spent this month so far",
            ),
            confidence=0.95,
            reason="llm_explicit_month_spending_scope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How have I spent this month so far", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant is None
    assert query_contract.filters.counterparty is None
    assert query_contract.aggregation is not None
    assert query_contract.aggregation.type == "sum"
    assert query_contract.answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_aggregate_continuation_without_reasoner_extraction_uses_deterministic_fresh_parse_to_drop_inherited_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 2, 27), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)
    parsed_extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="How much have I spent this month so far",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.7,
            reason="weak_aggregate_continuation_without_extraction",
        )

    def _fake_parse_deterministic(question: str, *, today: date, language: str = "en") -> QueryParseResult:
        del today, language
        assert question == "How much have I spent this month so far"
        return _ok_result(parsed_extraction, parsed_query)

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("deterministic fresh parse recovery should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much have I spent this month so far", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant is None
    assert query_contract.answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_income_followup_after_credit_list_preserves_active_credit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_income_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "credit"
    assert query_contract.aggregation is not None
    assert query_contract.aggregation.type == "sum"
    assert query_contract.result_limit is None
    assert query_contract.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_income_repair_followup_after_credit_list_preserves_active_credit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.91,
            reason="llm_income_repair_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "credit"
    assert query_contract.aggregation is not None
    assert query_contract.aggregation.type == "sum"
    assert query_contract.result_limit is None
    assert query_contract.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_income_vs_spending_followup_compiles_transaction_type_breakdown() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_income_vs_spending_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Compare the income vs spending", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is None or query_contract.filters.transaction_type is None
    assert query_contract.aggregation is not None
    assert query_contract.aggregation.type == "breakdown"
    assert query_contract.aggregation.group_by == "transaction_type"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_low_confidence_unclear_income_followup_clarifies_without_parser_reparse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = _contract(session_query)
    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_income_followup",
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("low-confidence unclear follow-up should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What's my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["flow_state"] == "parsing"


@pytest.mark.asyncio
async def test_unclear_income_repair_followup_uses_reasoner_compiler_without_parser_parse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 20)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = _contract(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="I mean my income this month",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.76,
            reason="repair_phrase_not_grounded",
            extraction=extraction,
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("compiler-safe reasoner extraction should not call parser.parse")

    def _fake_compile(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert parsed_extraction.raw_query == "I mean my income this month"
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]
    step.parser.compile_extraction = _fake_compile  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my income this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_contract.time_start == date(2026, 3, 1)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "credit"
    assert updates["current_page"] == 0
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_unclear_highest_single_transfer_repair_clarifies_without_explicit_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="I mean my highest single transfer",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="largest", limit=1),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.31,
            reason="repair_phrase_not_grounded",
        )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "I mean my highest single transfer"
        return _ok_result(extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I mean my highest single transfer", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
    assert updates["flow_state"] == "parsing"
    assert updates["session_active"] is True


@pytest.mark.asyncio
async def test_unclear_credit_pivot_followup_clarifies_without_grounded_reasoner_resolution() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        result_limit=5,
        result_reference="latest",
    )
    session_contract = _contract(session_query)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        raw_query="What about credit",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
        filters=Filters(transaction_type="credit"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.32,
            reason="credit_pivot_not_grounded",
        )

    async def _fake_parse(question: str, *, today: date, language: str) -> QueryParseResult:
        del today, language
        assert question == "What about credit"
        return _ok_result(extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fake_parse  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about credit", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")
    assert updates["flow_state"] == "parsing"
    assert updates["session_active"] is True


@pytest.mark.asyncio
async def test_dismissive_end_session_uses_localized_de_escalation_reply() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today, granularity="day"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="end_session",
            confidence=0.97,
            reason="dismissive_stop_helping_turn",
            end_session_kind="dismissive",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "get out", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert updates["response"] == render_message("query.session.dismissive_goodbye", "en")
    assert updates["session_active"] is False
    assert updates["flow_state"] == "complete"


@pytest.mark.asyncio
async def test_plain_recipient_summary_followup_reparses_as_new_beneficiary_summary_query() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 21)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
    )
    session_contract = _contract(session_query)

    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who did I send money to this month",
    )

    def _fake_resolve_existing(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        query = _query_ir(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21), granularity="month"),
            filters=Filters(transaction_type="debit"),
        )
        return _ok_result(parsed_extraction, query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="new_query",
            confidence=0.95,
            reason="recipient_summary_followup",
            extraction=extraction,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.resolve_existing_extraction = _fake_resolve_existing  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Who did I send money to this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"


@pytest.mark.asyncio
async def test_show_me_logs_semantic_reasoner_continuation_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    events: list[tuple[str, dict[str, object]]] = []
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    def _capture(event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="refine_existing",
            confidence=1.0,
            reason="llm_show_underlying_transactions",
            delta_type="none",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    await step._handle_continuation(
        {"message": "show me", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert (
        "query_continuation_resolution",
        {
            "path": "semantic_reasoner",
            "semantic_decision": "continuation",
            "continuation_type": "show_more",
            "followup_intent": "refine_existing",
            "delta_type": "none",
        },
    ) in events


@pytest.mark.asyncio
async def test_summary_contrastive_yesterday_without_reasoner_time_payload_reparses_message() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
            confidence=0.96,
            reason="llm_replace_scope_yesterday",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 18)
    assert query_contract.time_end == date(2026, 3, 18)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_three_days_without_reasoner_time_payload_reparses_message() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_period="last 3 days",
            confidence=0.96,
            reason="llm_replace_scope_last_three_days",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about last 3 days", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 16)
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_only_today_replaces_scope_and_preserves_filters() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
        filters=Filters(transaction_type="debit", merchant=["Mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=today, end=today, granularity="day"),
            delta_type="time",
            confidence=0.96,
            reason="llm_replace_scope_today",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "only today", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == today
    assert query_contract.time_end == today
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["Mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_single_item_contrastive_yesterday_preserves_latest_shape() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 16), end=today),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
            confidence=0.94,
            reason="llm_single_item_yesterday",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You last received a credit on March 17, 2026.",
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Salary from Acme Corp",
                        amount=950000.0,
                        date=date(2026, 3, 17),
                        metadata={"type": "credit", "bank_name": "First Bank"},
                    ).model_dump(mode="json")
                ],
                "surface_view": {
                    "mode": "direct_answer",
                    "context": {"type": "single_transaction"},
                },
            },
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 18)
    assert query_contract.time_end == date(2026, 3, 18)
    assert query_contract.result_limit == 1
    assert query_contract.result_reference == "latest"


@pytest.mark.asyncio
async def test_single_item_grounded_ask_clarify_recovers_to_yesterday_time_rescope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.extraction.logger.info", _capture)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 3, 16), end=today),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            answer_mode="ask_clarify",
            confidence=0.91,
            reason="llm_single_item_ask_clarify",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "No transaction yesterday?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You last received a credit on March 17, 2026.",
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Salary from Acme Corp",
                        amount=950000.0,
                        date=date(2026, 3, 17),
                        metadata={"type": "credit", "bank_name": "First Bank"},
                    ).model_dump(mode="json")
                ],
                "surface_view": {
                    "mode": "direct_answer",
                    "context": {"type": "single_transaction"},
                },
            },
            "current_page": 0,
            "show_expanded": False,
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "time_delta"
    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 18)
    assert query_contract.time_end == date(2026, 3, 18)
    assert query_contract.result_limit == 1
    assert query_contract.result_reference == "latest"
    assert (
        "query_single_item_followup",
        {
            "surface_type": "single_item",
            "session_mode": "active_result",
            "continuation_type": "time_delta",
            "followup_outcome": "time_rescope_query",
            "semantic_decision": "continuation",
        },
    ) in events


@pytest.mark.asyncio
async def test_summary_last_month_only_replaces_scope_and_preserves_debit_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 1, 1), end=today),
        filters=Filters(transaction_type="debit"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month"),
            delta_type="time",
            confidence=0.98,
            reason="llm_replace_scope_last_month",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last month only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 4,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 2, 1)
    assert query_contract.time_end == date(2026, 2, 28)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_summary_contrastive_last_week_correction_wrapper_replaces_scope_via_reasoner() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 16), end=today, granularity="week"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.98,
            reason="llm_replace_scope_last_week_correction",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "no, i meant last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.time_start == date(2026, 3, 9)
    assert query_contract.time_end == date(2026, 3, 15)
    assert query_contract.filters is not None
    assert query_contract.filters.transaction_type == "debit"
    assert query_contract.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_low_confidence_unclear_followup_requests_clarification() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 14)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 8), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.2,
            reason="ambiguous_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "for last week only", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["response"] == render_message("query.clarify.unsure_rephrase", "en")


@pytest.mark.asyncio
async def test_account_breakdown_followup_uses_selection_payload_without_surface_fallbacks() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account"),
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="refine_existing",
            drill_down_index=1,
            confidence=0.97,
            reason="payload_drill_down",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    query_result = QueryResult(
        summary_text="Spending by account — Mar 1 – Mar 28",
        items=[
            QueryResultItem(
                id="acc_1",
                description="Zenith Bank",
                amount=-1099852,
                date=today,
                metadata={"count": 23, "key": "Zenith Bank"},
            ),
            QueryResultItem(
                id="acc_2",
                description="First Bank",
                amount=-96200,
                date=today,
                metadata={"count": 5, "key": "First Bank"},
            ),
        ],
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="acc_1",
                    label="Zenith Bank",
                    amount=-1099852,
                    count=23,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="acc_1",
                        label="Zenith Bank",
                        group_by="account",
                        group_key="Zenith Bank",
                        filters_patch={"account_filter": "Zenith Bank"},
                    ),
                ),
                SurfaceItemView(
                    id="acc_2",
                    label="First Bank",
                    amount=-96200,
                    count=5,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="acc_2",
                        label="First Bank",
                        group_by="account",
                        group_key="First Bank",
                        filters_patch={"account_filter": "First Bank"},
                    ),
                ),
            ],
        ),
    )

    updates = await step._handle_continuation(
        {"message": "show all for first bank", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_contract": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_contract = updates["query_contract"]
    assert query_contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_contract.query_operation == QueryOperation.LIST_TRANSACTIONS
    assert query_contract.filters is not None
    assert query_contract.filters.account_filter == "First Bank"
    assert query_contract.aggregation is None
