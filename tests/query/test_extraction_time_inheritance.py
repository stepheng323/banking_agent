from datetime import date, timedelta
from typing import Any

import pytest
from langchain_core.runnables import Runnable

from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.actions import handle_drill_down
from banking.transactions.query.continuations.compiler_paths import _apply_router_insight_hint
from banking.transactions.query.continuations.result_paths import _resolve_coverage_intent
from banking.transactions.query.contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryAnswerStrategy,
    QueryRequest,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryParseResult,
    QueryRequestShape,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.models.operations import GroupedSummarySpec, RetrieveOperation, SummarizeOperation
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.presentation.surface_builder import build_surface_view
from banking.transactions.query.services.reasoning.models import QuerySemanticDecision
from tests.query.factories import make_query_request


def _query_ir(**kwargs: Any) -> QueryRequest:
    fallback_day = date(2026, 3, 4)
    defaults: dict[str, Any] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return QueryExtractionResult(raw_query="fallback")


class _DummyLLM(Runnable[Any, Any]):
    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return ""

    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _contract(
    query: QueryRequest,
    *,
    comparison: Any | None = None,
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
) -> QueryRequest:
    del comparison, continuation_type, continuation_delta_type
    assert query.time_range is not None
    return query.model_copy(deep=True)


def _ok_result(extraction: QueryExtractionResult, query: QueryRequest) -> QueryParseResult:
    contract = _contract(query)
    return QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=extraction,
        query_request=contract.model_dump(mode="json"),
    )


def test_router_insight_hint_prevents_generic_cash_flow_parser_downgrade() -> None:
    today = date(2026, 7, 27)
    parsed = QueryExtractionResult(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        request_shape=QueryRequestShape.ANALYTICS,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who received the largest share of my spending this month?",
    )
    result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=parsed)
    captured: dict[str, QueryExtractionResult] = {}

    class _Parser:
        def compile_extraction(
            self,
            extraction: QueryExtractionResult,
            *,
            today: date,
            language: str,
        ) -> QueryParseResult:
            del today, language
            captured["extraction"] = extraction
            return QueryParseResult(outcome=ResolverOutcome.OK, extraction=extraction)

    class _Step:
        parser = _Parser()

    updated = _apply_router_insight_hint(
        _Step(),
        result,
        {"query_insight_type": "counterparty_concentration"},
        today=today,
        language="en",
    )

    assert updated.extraction is not None
    assert updated.extraction.intent == QueryIntent.INSIGHT
    assert updated.extraction.request_shape == QueryRequestShape.INSIGHT
    assert updated.extraction.insight is not None
    assert updated.extraction.insight.insight_type == "counterparty_concentration"
    assert updated.extraction.time_range.period == "this_month"
    assert captured["extraction"] == updated.extraction


def test_router_insight_hint_does_not_override_send_to_recipient() -> None:
    """A concentration router hint must not clobber a confident beneficiary-summary intent."""
    today = date(2026, 7, 27)
    parsed = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        request_shape=QueryRequestShape.ANALYTICS,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who did I send money to the most this month?",
    )
    result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=parsed)

    class _Parser:
        def compile_extraction(self, extraction: QueryExtractionResult, *, today: date, language: str) -> Any:
            pytest.fail("compile_extraction should not be called when parser intent is preserved")

    class _Step:
        parser = _Parser()

    updated = _apply_router_insight_hint(
        _Step(),
        result,
        {"query_insight_type": "counterparty_concentration"},
        today=today,
        language="en",
    )

    assert updated.extraction is not None
    assert updated.extraction.intent == QueryIntent.BENEFICIARY_SUMMARY


def test_router_insight_hint_survives_cash_flow_normalization() -> None:
    """A router-selected insight remains authoritative through the real compiler."""
    today = date(2026, 7, 27)
    parsed = QueryExtractionResult(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        request_shape=QueryRequestShape.ANALYTICS,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who received the largest share of my spending this month?",
    )
    result = QueryParseResult(outcome=ResolverOutcome.OK, extraction=parsed)

    class _Step:
        parser = ExtractionStep(_DummyLLM()).parser

    updated = _apply_router_insight_hint(
        _Step(),
        result,
        {"query_insight_type": "counterparty_concentration"},
        today=today,
        language="en",
    )

    assert updated.extraction is not None
    assert updated.extraction.intent == QueryIntent.INSIGHT
    assert updated.query_request is not None
    assert updated.query_request["operation"]["kind"] == "analyze"
    assert updated.query_request["operation"]["analysis"]["insight_type"] == "counterparty_concentration"


@pytest.mark.asyncio
async def test_parse_new_query_does_not_inherit_time_range_for_unspecified_time() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 4)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show my transfers",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=29), end=today, granularity="day"),
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
                "query_request": session_contract.model_dump(),
            },
        }
    )

    query_request = updates["query_request"]
    assert query_request.time_start == today - timedelta(days=29)
    assert query_request.time_end == today


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
        intent=QueryIntent.BENEFICIARY_SUMMARY,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_request.time_start == date(2026, 3, 2)
    assert query_request.time_end == today


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
        query_request=session_contract,
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
            "query_request": session_contract.model_dump(),
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
            time_range=TimeRange(start=today - timedelta(days=29), end=today),
        )
        return _ok_result(
            QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": QueryResult(
                summary_text="That transaction was on March 28, 2026.",
                items=[],
                query_request=session_contract,
                surface_view=SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context={"type": "single_transaction"}),
            ).model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "list"
    assert query_request.filters is None or query_request.filters.counterparty is None
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
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": QueryResult(
                summary_text="That transaction was on March 28, 2026.",
                items=[],
                query_request=session_contract,
                surface_view=SurfaceView(mode=SurfaceViewMode.DIRECT_ANSWER, context={"type": "single_transaction"}),
            ).model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.filters is None or query_request.filters.counterparty is None
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 8)
    assert query_request.time_end == date(2026, 3, 14)
    assert query_request.filters is not None
    assert query_request.filters.merchant == ["Mum"]
    assert query_request.filters.transaction_type == "debit"
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    assert updates["current_page"] == 2
    assert "query_request" not in updates


@pytest.mark.asyncio
async def test_previous_pagination_only_moves_back_without_scope_mutation() -> None:
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
            followup_intent="previous_pagination",
            confidence=0.97,
            reason="llm_previous_pagination",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "back", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
        },
    )

    assert updates["current_page"] == 1
    assert "query_request" not in updates


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
            "query_request": session_contract.model_dump(),
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "recipient_drill_down"
    assert updates["query_request"].filters is not None
    assert updates["query_request"].filters.counterparty == ["Gaines"]


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.aggregation is None
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Adesanya Kunle"]


@pytest.mark.asyncio
async def test_focused_beneficiary_fact_followup_uses_selection_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=5),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="*Top Senders* — This Month",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            ),
            QueryResultItem(
                id="bene_2",
                description="Techcorp Nigeria Ltd",
                amount=850000,
                date=today,
                metadata={"count": 1, "recipient_name": "Techcorp Nigeria Ltd"},
            ),
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="date",
            confidence=0.98,
            reason="semantic_focused_beneficiary_fact",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_focused_beneficiary_this_referential_fact_followup_uses_selection_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=5),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            ),
            QueryResultItem(
                id="bene_2",
                description="Techcorp Nigeria Ltd",
                amount=850000,
                date=today,
                metadata={"count": 1, "recipient_name": "Techcorp Nigeria Ltd"},
            ),
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="date",
            confidence=0.98,
            reason="semantic_focused_beneficiary_referential_fact",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_focused_beneficiary_requested_field_followup_normalizes_to_answer_fact() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            requested_field="date",
            confidence=0.98,
            reason="semantic_requested_date_fact_without_action",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.aggregation is None


@pytest.mark.asyncio
async def test_focused_beneficiary_drilldown_without_fact_field_scopes_to_transactions() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 28)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            confidence=0.98,
            reason="User refers to the focused beneficiary item and asks for its date fact.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.answer_fact_field is None
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_stale_focused_beneficiary_payload_is_repaired_to_scoped_transactions() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 28)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    stale_payload = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id="bene_1",
        label="Acme Corp",
        fact_capabilities=["date", "amount", "bank", "counterparty"],
    )
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            lead_text="Acme Corp sent you the most this month: ₦950,000.",
            items=[
                SurfaceItemView(
                    id="bene_1",
                    label="Acme Corp",
                    amount=950000,
                    count=1,
                    payload=stale_payload,
                    metadata={"count": 1, "recipient_name": "Acme Corp"},
                )
            ],
            context={
                "type": "focused_beneficiary",
                "focus_type": "beneficiary",
                "selected_item_id": "bene_1",
                "selected_payload": stale_payload.model_dump(mode="json"),
            },
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            confidence=0.98,
            reason="User refers to the currently focused beneficiary item and asks for its date fact.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_focused_beneficiary_fact_followup_repairs_missing_surface_items_from_result_item() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 28)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            lead_text="Acme Corp sent you the most this month: ₦950,000.",
            context={
                "type": "focused_beneficiary",
                "focus_type": "beneficiary",
                "selected_item_id": "bene_1",
            },
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_action="answer_fact",
            fact_field="date",
            confidence=0.98,
            reason="User refers to the focused beneficiary item and asks for its date fact.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert updates["_query_session_transition"] == "replace_session_new_query"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.filters.transaction_type == "credit"
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_generic_direct_answer_beneficiary_payload_is_repaired_from_contract() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 28)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="credit"),
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    stale_payload = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id="bene_1",
        label="Acme Corp",
        fact_capabilities=["date", "amount", "bank", "counterparty"],
    )
    query_result = QueryResult(
        summary_text="Acme Corp sent you the most this month: ₦950,000.",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            lead_text="Acme Corp sent you the most this month: ₦950,000.",
            items=[
                SurfaceItemView(
                    id="bene_1",
                    label="Acme Corp",
                    amount=950000,
                    count=1,
                    payload=stale_payload,
                    metadata={"count": 1, "recipient_name": "Acme Corp"},
                )
            ],
            context={"type": "single_transaction", "selected_item_id": "bene_1"},
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            confidence=0.98,
            reason="User refers to the focused item and asks for its date fact.",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was this", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Acme Corp"]
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_focused_category_fact_followup_uses_selection_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="category", sort_by="amount", limit=5),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="Shopping took the most this month: ₦620,000.",
        items=[
            QueryResultItem(
                id="cat_shopping",
                description="Shopping",
                amount=620000,
                date=today,
                metadata={"count": 3, "key": "shopping"},
            ),
            QueryResultItem(
                id="cat_transfers",
                description="Transfers",
                amount=437000,
                date=today,
                metadata={"count": 19, "key": "transfers"},
            ),
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="reference",
            confidence=0.98,
            reason="semantic_focused_category_fact",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Reference?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "reference"
    assert query_request.filters is not None
    assert query_request.filters.category == ["shopping"]
    assert query_request.filters.transaction_type == "debit"
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_focused_account_fact_followup_uses_selection_scope() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account", sort_by="amount", limit=5),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="GTBank had the highest outflow this month: ₦120,000.",
        items=[
            QueryResultItem(
                id="acct_gtb",
                description="GTBank",
                amount=120000,
                date=today,
                metadata={"count": 4, "key": "GTBank"},
            ),
            QueryResultItem(
                id="acct_access",
                description="Access Bank",
                amount=80000,
                date=today,
                metadata={"count": 2, "key": "Access Bank"},
            ),
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="date",
            confidence=0.98,
            reason="semantic_focused_account_fact",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert isinstance(query_request.operation, RetrieveOperation)
    assert query_request.operation.projection.shape == "fact"
    assert query_request.answer_fact_field == "date"
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "GTBank"
    assert query_request.filters.transaction_type == "debit"
    assert query_request.aggregation is None
    assert query_request.result_limit is None


@pytest.mark.asyncio
async def test_direct_answer_recipient_delta_follow_up_reuses_scope_and_swaps_counterparty() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 4, 6)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 4, 1), end=today),
        filters=Filters(counterparty=["Mum"], transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="recipient_drill_down",
            followup_intent="none",
            recipient_name="tolu",
            delta_type="filter",
            confidence=0.98,
            reason="deterministic_scoped_recipient_delta",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about tolu?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You paid Mum on April 03, 2026.",
                "items": [],
                "surface_view": {"mode": "direct_answer", "context": {"type": "single_transaction"}},
            },
        },
    )

    query_request = updates["query_request"]
    assert updates["flow_state"] == "executing"
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.time_start == date(2026, 4, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["tolu"]
    assert query_request.filters.transaction_type == "debit"


@pytest.mark.asyncio
async def test_mislabelled_recipient_drilldown_cannot_replay_focused_transaction() -> None:
    """Provider adapters may call a recipient scope change a generic drill-down."""
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 8, 1)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 7, 1), end=today),
        filters=Filters(counterparty=["Tolu Adebayo"], transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
        answer_fact_field="date",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            # This is the malformed/legacy adapter shape that caused the
            # current focused transaction to be rendered for the new person.
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="date",
            recipient_name="Mum",
            confidence=0.98,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about mum?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "The last time you paid Tolu Adebayo was July 21, 2026.",
                "items": [
                    {
                        "id": "tolu-1",
                        "description": "Transfer to Tolu Adebayo",
                        "amount": 2000,
                        "date": "2026-07-21",
                        "metadata": {"recipient_name": "Tolu Adebayo", "bank_name": "Access Bank"},
                    }
                ],
                "surface_view": {"mode": "direct_answer", "context": {"type": "single_transaction"}},
            },
        },
    )

    query_request = updates["query_request"]
    assert updates["continuation_type"] == "recipient_drill_down"
    assert updates["flow_state"] == "executing"
    assert query_request.filters is not None
    assert query_request.filters.counterparty == ["Mum"]
    assert query_request.answer_fact_field == "date"
    assert query_request.result_reference == "latest"
    assert "selected_item_index" not in updates


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    assert updates["query_request"].intent == QueryIntent.TRANSACTION_LIST
    assert updates["query_request"].time_start == date(2026, 3, 1)
    assert updates["query_request"].time_end == today
    assert updates["query_request"].answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["resolver_message"] is None


@pytest.mark.asyncio
async def test_recheck_follow_up_reruns_existing_analytics_summary_without_reparse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 9)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.91,
            reason="bad_reasoner_reparsed_repeat_as_fresh_query",
            extraction=QueryExtractionResult(raw_query="check againo"),
        )

    async def _fail_parse_new_query(state: dict[str, Any]) -> dict[str, Any]:
        del state
        raise AssertionError("repeat follow-up must not reparse as a new query")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step._parse_new_query = _fail_parse_new_query  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Check againo", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "You spent ₦20,000 today, across 2 transactions.", "items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.time_start == today
    assert query_request.time_end == today
    assert updates["flow_state"] == "executing"
    assert updates["session_active"] is True
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert updates["continuation_type"] == "repeat_query"


@pytest.mark.asyncio
async def test_recheck_follow_up_preserves_count_query_shape() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 9)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="refine_existing",
            confidence=0.92,
            reason="llm_repeat_existing_query",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Check again", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "You didn't make any transactions today.", "items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == today
    assert query_request.time_end == today
    assert updates["flow_state"] == "executing"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_query",
    [
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 6, 9), end=date(2026, 6, 9)),
        ),
        _query_ir(
            intent=QueryIntent.TRANSACTION_SEARCH,
            time_range=TimeRange(start=date(2026, 6, 9), end=date(2026, 6, 9)),
            filters=Filters(counterparty=["Tolu Adebayo"]),
        ),
        _query_ir(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 9)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum", sort_by="amount"),
        ),
        _query_ir(
            intent=QueryIntent.TIME_COMPARISON,
            time_range=TimeRange(start=date(2026, 6, 9), end=date(2026, 6, 9)),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
        ),
    ],
)
async def test_recheck_follow_up_reruns_any_active_query_request_without_reparse(session_query: QueryRequest) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 9)
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.9,
            reason="bad_reasoner_reparsed_repeat_as_fresh_query",
            extraction=QueryExtractionResult(raw_query="check again"),
        )

    async def _fail_parse_new_query(state: dict[str, Any]) -> dict[str, Any]:
        del state
        raise AssertionError("repeat follow-up must not reparse as a new query")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step._parse_new_query = _fail_parse_new_query  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Check again", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "Previous answer", "items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == session_contract.intent
    assert query_request.time_start == session_contract.time_start
    assert query_request.time_end == session_contract.time_end
    assert updates["flow_state"] == "executing"
    assert updates["session_active"] is True
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert updates["continuation_type"] == "repeat_query"


@pytest.mark.asyncio
async def test_show_evidence_follow_up_converts_aggregate_summary_to_scoped_transactions_and_clears_fact_anchor() -> (
    None
):
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
            "query_request": session_contract.model_dump(),
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

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.aggregation is None
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.answer_fact_field is None
    assert query_request.result_reference is None
    assert updates.get("conversational_prefix") is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_show_evidence_follow_up_converts_cashflow_summary_to_scoped_transactions() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_evidence",
            followup_intent="refine_existing",
            confidence=0.96,
            reason="llm_show_cashflow_evidence",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "show the transactions behind that", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "₦2,506,250 came in and ₦1,196,052 went out.",
                "items": [],
            },
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.aggregation is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_coverage_follow_up_over_transaction_list_returns_completeness_answer() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 2, 28), end=today),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="coverage",
            followup_intent="none",
            answer_mode="ask_clarify",
            confidence=0.94,
            reason="llm_list_completeness_check",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "is this all?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "I found 37 transactions in the last 30 days.",
                "has_more": True,
                "query_request": session_contract.model_dump(),
                "items": [
                    QueryResultItem(
                        id="txn_001",
                        description="Transfer to Mum",
                        amount=50000,
                        date=date(2026, 3, 26),
                        metadata={"type": "debit", "bank_name": "GTBank"},
                    ).model_dump(mode="json")
                ],
            },
            "current_page": 1,
            "page_size": 5,
        },
    )

    assert updates["flow_state"] == "complete"
    assert updates["session_active"] is True
    assert "not the complete list" in updates["response"]
    assert "more results available" in updates["response"]


def test_coverage_discriminator_uses_typed_semantics_with_structural_legacy_default() -> None:
    list_contract = _contract(_query_ir(intent=QueryIntent.TRANSACTION_LIST))
    assert (
        _resolve_coverage_intent(
            QuerySemanticDecision(decision="continuation", coverage_intent="data_coverage"),
            list_contract,
        )
        == "data_coverage"
    )
    assert (
        _resolve_coverage_intent(
            QuerySemanticDecision(decision="continuation", coverage_intent="ambiguous"),
            list_contract,
        )
        == "ambiguous"
    )
    assert (
        _resolve_coverage_intent(QuerySemanticDecision(decision="continuation"), list_contract) == "result_completeness"
    )


@pytest.mark.asyncio
async def test_focused_detail_followups_answer_fact_then_original_list_completeness() -> None:
    """Regression: details must not swallow the list's fact or completeness context."""
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 29)
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=today),
        )
    )
    selected_item = QueryResultItem(
        id="txn_salary",
        description="Salary from Acme Corp",
        amount=950000,
        date=date(2026, 3, 8),
        metadata={"bank_name": "GTBank", "transaction_type": "credit", "status": "posted"},
    )
    detail_result = QueryResult(
        summary_text="I found 43 transactions this month.",
        items=[selected_item],
        has_more=True,
        query_request=session_contract,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id=selected_item.id,
                    label=selected_item.description,
                    amount=selected_item.amount,
                    payload=SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id=selected_item.id,
                        label=selected_item.description,
                    ),
                    metadata=selected_item.metadata or {},
                )
            ],
            context={
                "type": "single_transaction",
                "selected_item_id": selected_item.id,
                "parent_visible_count": 5,
            },
        ),
    )
    session = {
        "session_active": True,
        "query_request": session_contract.model_dump(),
        "query_result": detail_result.model_dump(mode="json"),
        "selected_item_id": selected_item.id,
        "current_page": 0,
        "page_size": 5,
    }
    semantic_decisions = iter(
        (
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="drill_down",
                followup_intent="none",
                drill_down_action="answer_fact",
                drill_down_index=0,
                fact_field="bank",
                requested_field="bank",
                confidence=0.98,
                reason="semantic focused fact request",
            ),
            QuerySemanticDecision(
                decision="continuation",
                continuation_type="coverage",
                followup_intent="none",
                confidence=0.98,
                reason="semantic result completeness request",
            ),
        )
    )

    async def _semantic_reason(_: object) -> QuerySemanticDecision:
        return next(semantic_decisions)

    step.reasoner.reason = _semantic_reason  # type: ignore[method-assign]

    fact_updates = await step._handle_continuation(
        {"message": "What bank was that?", "today": today, "language": "en"}, session
    )

    assert fact_updates["drill_down_action"] == "answer_fact"
    fact_result = await handle_drill_down({"language": "en", "query_result": detail_result, **fact_updates})
    assert fact_result.outcome == TransactionOutcome.OK
    assert "GTBank" in (fact_result.response or "")
    assert "Amount:" not in (fact_result.response or "")

    completeness_updates = await step._handle_continuation(
        {"message": "Is that everything?", "today": today, "language": "en"}, session
    )

    assert completeness_updates["flow_state"] == "complete"
    assert "not the complete list" in completeness_updates["response"]
    assert "5 matching transactions" in completeness_updates["response"]


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
            "query_request": session_contract.model_dump(),
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
        time_range=TimeRange(start=date(2026, 2, 27), end=today),
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
            "query_request": session_contract.model_dump(),
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

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.aggregation is None
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "First Bank"
    assert query_request.filters.transaction_type == "debit"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_account_breakdown_drilldown_prefers_explicit_label_over_ordinal_index_hint() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 28)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 2, 27), end=today),
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
            "query_request": session_contract.model_dump(),
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

    query_request = updates["query_request"]
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "First Bank"


@pytest.mark.asyncio
async def test_show_me_follow_up_increments_pagination_on_summary_intent() -> None:
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
            reason="pagination_on_summary",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "more", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": [], "has_more": True},
            "current_page": 0,
        },
    )

    assert updates["current_page"] == 1
    assert updates["continuation_type"] == "show_more"


@pytest.mark.asyncio
async def test_show_more_on_final_page_keeps_page_and_explains_boundary() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_contract = _contract(
        _query_ir(intent=QueryIntent.TRANSACTION_LIST, time_range=TimeRange(start=today, end=today))
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="continue_pagination",
            confidence=0.99,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    updates = await step._handle_continuation(
        {"message": "more", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": [], "has_more": False},
            "current_page": 2,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert updates["current_page"] == 2
    assert updates["response"] == "That's the full list for this search."


@pytest.mark.asyncio
async def test_previous_on_first_page_keeps_page_and_explains_boundary() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 6)
    session_contract = _contract(
        _query_ir(intent=QueryIntent.TRANSACTION_LIST, time_range=TimeRange(start=today, end=today))
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="show_more",
            followup_intent="previous_pagination",
            confidence=0.99,
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    updates = await step._handle_continuation(
        {"message": "previous", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": [], "has_more": True},
            "current_page": 0,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert updates["current_page"] == 0
    assert updates["response"] == "You're already on the first page."


@pytest.mark.asyncio
async def test_show_them_after_count_summary_recovers_from_fresh_query_label() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    yesterday = date(2026, 3, 18)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=yesterday, end=yesterday, granularity="day"),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            continuation_type="unclear",
            confidence=0.42,
            reason="llm_mislabeled_show_existing_transactions_as_fresh_query",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Show them", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.aggregation is None
    assert query_request.time_start == yesterday
    assert query_request.time_end == yesterday
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("as_refinement", [False, True], ids=["fresh-query-recovery", "refine-existing"])
async def test_show_them_after_top_beneficiary_lists_only_that_beneficiary_transactions(
    as_refinement: bool,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 7, 13)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="debit"),
        time_range=TimeRange(start=date(2026, 7, 1), end=today, granularity="month"),
        aggregation=Aggregation(type="sum", sort_by="amount", limit=1),
        result_limit=1,
    )
    session_contract = _contract(session_query)
    query_result = QueryResult(
        summary_text="You sent Cowrywise the most this month, with ₦150,000 across 3 transfers.",
        items=[
            QueryResultItem(
                id="beneficiary-cowrywise",
                description="Cowrywise",
                amount=150000,
                date=today,
                metadata={"count": 3, "recipient_name": "Cowrywise"},
            )
        ],
        query_request=session_contract,
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )
    query_result.surface_view = build_surface_view(query_result)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        if as_refinement:
            return QuerySemanticDecision(
                decision="continuation",
                continuation_type="show_more",
                followup_intent="refine_existing",
                confidence=0.96,
                reason="show_underlying_beneficiary_transactions",
            )
        return QuerySemanticDecision(
            decision="fresh_query",
            continuation_type="unclear",
            confidence=0.42,
            reason="llm_mislabeled_beneficiary_evidence_request_as_fresh_query",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Show them", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
            "current_page": 0,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.aggregation is None
    assert query_request.time_start == date(2026, 7, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.counterparty == ["Cowrywise"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert first_updates["query_request"].intent == QueryIntent.TRANSACTION_LIST
    assert first_updates["query_request"].time_start == date(2026, 2, 17)
    assert first_updates["query_request"].time_end == today
    assert first_updates["current_page"] == 0

    second_updates = await step._handle_continuation(
        {"message": "Only this week's", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": first_updates["query_request"].model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = second_updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.time_start == date(2026, 3, 16)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
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
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 9)
    assert query_request.time_end == date(2026, 3, 15)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_semantic_unclear_time_signal_recovers_scoped_gtbank_no_result_followup() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 6, 23), end=today, granularity="week"),
        filters=Filters(account_filter="GTBank"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            ),
            confidence=0.91,
            reason="semantic_time_scope_in_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What of last week", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "No GTBank transactions found for this week.",
                "items": [],
            },
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.time_start == date(2026, 6, 15)
    assert query_request.time_end == date(2026, 6, 21)
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "GTBank"
    assert updates["continuation_type"] == "time_delta"
    assert updates["continuation_delta_type"] == "time"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.parametrize(
    ("message", "language"),
    [
        ("yesterday nko", "pcm"),
        ("ti ana nko", "yo"),
        ("na jiya fa", "ha"),
    ],
)
@pytest.mark.asyncio
async def test_semantic_multilingual_time_signal_preserves_active_scope(message: str, language: str) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 6, 23), end=today, granularity="week"),
        filters=Filters(account_filter="GTBank"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            time_range=TimeRange(start=date(2026, 6, 26), end=date(2026, 6, 26), granularity="day"),
            confidence=0.88,
            reason="multilingual_time_scope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": message, "today": today, "language": language},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 6, 26)
    assert query_request.time_end == date(2026, 6, 26)
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "GTBank"
    assert updates["continuation_type"] == "time_delta"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_unclear_non_time_followup_does_not_recover_to_time_delta() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 27)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 6, 23), end=today, granularity="week"),
        filters=Filters(account_filter="GTBank"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.92,
            reason="non_time_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "what of it", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    assert "query_request" not in updates
    assert updates["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    assert updates["session_active"] is True


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

    monkeypatch.setattr("banking.transactions.query.nodes.extraction.logger.info", _capture)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
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

    monkeypatch.setattr("banking.transactions.query.nodes.extraction.logger.info", _capture)

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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 9)
    assert query_request.time_end == date(2026, 3, 15)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert (
        "query_continuation_resolution",
        {
            "path": "time_rescope_recovery",
            "trigger_reason": "low_confidence_unclear",
            "recovered": True,
            "session_has_query_request": True,
            "resolved_time_range": True,
            "resolved_time_start": "2026-03-09",
            "resolved_time_end": "2026-03-15",
            "preserved_query_shape": True,
            "skip_reason": None,
        },
    ) in events


@pytest.mark.asyncio
async def test_assertive_yesterday_correction_rescopes_active_count_from_reasoner_decision() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    reasoner_calls = 0

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        nonlocal reasoner_calls
        reasoner_calls += 1
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            extraction=QueryExtractionResult(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            ),
            confidence=0.96,
            reason="llm_assertive_yesterday_time_rescope",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I said yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"summary_text": "You made 4 transaction(s) today.", "items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert reasoner_calls == 1


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 9)
    assert query_request.time_end == date(2026, 3, 15)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.answer_fact_field is None
    assert query_request.result_limit is None
    assert query_request.result_reference is None
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
                intent=QueryIntent.ANALYTICS_SUMMARY,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant is None
    assert query_request.filters.counterparty is None
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_aggregate_continuation_without_reasoner_extraction_uses_deterministic_fresh_parse_to_drop_inherited_filter() -> (
    None
):
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
        intent=QueryIntent.ANALYTICS_SUMMARY,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant is None
    assert query_request.answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_aggregate_continuation_with_polluted_reasoner_extraction_prefers_clean_fresh_parse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 30)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", merchant=["mum"]),
        result_limit=1,
        result_reference="latest",
        answer_fact_field="date",
    )
    session_contract = _contract(session_query)
    parsed_extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
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
            confidence=0.92,
            reason="polluted_reasoner_aggregate_extraction",
            extraction=QueryExtractionResult(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=QueryFilters(recipient="mum", transaction_type="debit"),
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
                request_shape=QueryRequestShape.FACT,
                answer_fact_field="amount",
                result_reference="latest",
                raw_query="How much have I spent this month so far",
            ),
        )

    def _fake_parse_deterministic(question: str, *, today: date, language: str = "en") -> QueryParseResult:
        del today, language
        assert question == "How much have I spent this month so far"
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much have I spent this month so far", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant is None
    assert query_request.answer_fact_field is None
    assert query_request.result_reference is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_beneficiary_summary_aggregate_followup_prefers_clean_total_parse() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 30)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum", sort_by="count"),
    )
    session_contract = _contract(session_query)
    parsed_extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="How much did I send in total this month",
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
            confidence=0.93,
            reason="beneficiary_summary_total_followup",
            extraction=QueryExtractionResult(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                filters=QueryFilters(transaction_type="debit"),
                time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
                request_shape=QueryRequestShape.GROUPED_SUMMARY,
                aggregation={"type": "sum", "sort_by": "count"},
                raw_query="How much did I send in total this month",
            ),
        )

    def _fake_parse_deterministic(question: str, *, today: date, language: str = "en") -> QueryParseResult:
        del today, language
        assert question == "How much did I send in total this month"
        return _ok_result(parsed_extraction, parsed_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much did I send in total this month", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.aggregation.sort_by != "count"
    assert updates["_query_session_transition"] == "replace_session_new_query"


@pytest.mark.asyncio
async def test_grouped_total_followup_rebuilds_scoped_sum_from_beneficiary_summary() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 30)
    session_query = _query_ir(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum", sort_by="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="grouped_total_followup",
            followup_intent="refine_existing",
            confidence=0.95,
            reason="llm_grouped_total_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "so what the total?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.aggregation.sort_by != "count"
    assert query_request.result_reference is None
    assert query_request.answer_fact_field is None
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "credit"
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.result_limit is None
    assert query_request.result_reference is None
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "credit"
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"
    assert query_request.result_limit is None
    assert query_request.result_reference is None
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is None or query_request.filters.transaction_type is None
    assert query_request.aggregation is not None
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "transaction_type"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "language"),
    [
        ("What about income?", "en"),
        ("Income nko?", "pcm"),
        ("Bawo ni owo to wole?", "yo"),
        ("Yaya batun kudin shiga?", "ha"),
        ("Kedu maka ego batara?", "ig"),
        ("Switch that same summary to money coming in", "en"),
    ],
)
async def test_typed_income_direction_followup_preserves_monthly_summary_scope(
    message: str,
    language: str,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 7, 16)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 7, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit", account_filter="GTBank", status="successful"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="refine_existing",
            transaction_direction_delta="credit",
            confidence=0.93,
            reason="semantic_direction_refinement",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": message, "today": today, "language": language},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 7, 1)
    assert query_request.time_end == today
    assert query_request.filters.transaction_type == "credit"
    assert query_request.filters.account_filter == "GTBank"
    assert query_request.filters.status == "successful"
    assert query_request.aggregation.type == "sum"
    assert updates["continuation_type"] == "filter_delta"
    assert updates.get("conversational_prefix") is None
    assert updates["current_page"] == 0


@pytest.mark.asyncio
async def test_typed_both_directions_followup_builds_direction_breakdown() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 7, 16)
    session_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 7, 1), end=today, granularity="month"),
            filters=Filters(transaction_type="debit", account_filter="GTBank"),
            aggregation=Aggregation(type="sum"),
        )
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            transaction_direction_delta="both",
            confidence=0.95,
            reason="semantic_both_directions",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    updates = await step._handle_continuation(
        {"message": "Compare both directions", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.filters.transaction_type is None
    assert query_request.filters.account_filter == "GTBank"
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "transaction_type"


@pytest.mark.asyncio
async def test_account_breakdown_followup_after_credit_total_preserves_credit_scope_and_time() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 29)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        raw_query="Break down by account",
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        filters=QueryFilters(transaction_type="debit"),
        aggregation=QueryAggregation(type="breakdown", group_by="account"),
        request_shape=QueryRequestShape.ANALYTICS,
    )
    extracted_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today - timedelta(days=29), end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="semantic_grouping_refinement",
            extraction=extraction,
        )

    def _fake_compile(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert parsed_extraction.raw_query == "Break down by account"
        return _ok_result(parsed_extraction, extracted_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.compile_extraction = _fake_compile  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Break down by account", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 6, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "credit"
    assert query_request.aggregation is not None
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "account"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_account_breakdown_followup_uses_deterministic_contract_when_reasoner_has_no_extraction() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 7, 7)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 7, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="credit"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)
    deterministic_extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        raw_query="Break down by account",
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        filters=QueryFilters(transaction_type="debit"),
        aggregation=QueryAggregation(type="breakdown", group_by="account"),
        request_shape=QueryRequestShape.ANALYTICS,
    )
    deterministic_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today - timedelta(days=29), end=today, granularity="day"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="breakdown", group_by="account"),
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="semantic_grouping_refinement",
        )

    def _fake_parse_deterministic(
        message: str,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert message == "Break down by account"
        return _ok_result(deterministic_extraction, deterministic_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse_deterministic = _fake_parse_deterministic  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Break down by account", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 7, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "credit"
    assert query_request.aggregation is not None
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "account"


@pytest.mark.asyncio
async def test_compare_to_income_after_spending_total_compiles_cashflow_summary() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 29)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="sum"),
    )
    session_contract = _contract(session_query)
    extraction = QueryExtractionResult(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        raw_query="Compare to how much came in",
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        request_shape=QueryRequestShape.ANALYTICS,
    )
    extracted_query = _query_ir(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        time_range=TimeRange(start=date(2026, 6, 1), end=today, granularity="month"),
        filters=None,
        aggregation=None,
    )

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.96,
            reason="semantic_cashflow_compare",
            extraction=extraction,
        )

    def _fake_compile_reasoner(
        parsed_extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        del today, language
        assert parsed_extraction.intent == QueryIntent.CASH_FLOW_SUMMARY
        assert parsed_extraction.raw_query == "Compare to how much came in"
        return _ok_result(parsed_extraction, extracted_query)

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.compile_reasoner_extraction = _fake_compile_reasoner  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Compare to how much came in", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "You spent ₦1,460,052 this month, across 52 transactions.",
                "items": [],
                "surface_view": {
                    "mode": "direct_answer",
                    "context": {"type": "summary_scope", "focus_type": "summary_scope"},
                },
            },
            "current_page": 0,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 6, 1)
    assert query_request.time_end == today
    assert query_request.filters is None or query_request.filters.transaction_type is None
    assert query_request.aggregation is not None
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "transaction_type"
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
            "query_request": session_contract.model_dump(),
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
        intent=QueryIntent.ANALYTICS_SUMMARY,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "credit"
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
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="I mean my highest single transfer",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 3, 1), end=today, granularity="month"),
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
            "query_request": session_contract.model_dump(),
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
        intent=QueryIntent.TRANSACTION_LIST,
        raw_query="What about credit",
    )
    parsed_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=29), end=today, granularity="day"),
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
            "query_request": session_contract.model_dump(),
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
            "query_request": session_contract.model_dump(),
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
        intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"


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

    monkeypatch.setattr("banking.transactions.query.nodes.extraction.logger.info", _capture)

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
            "query_request": session_contract.model_dump(),
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
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False


@pytest.mark.asyncio
async def test_direct_time_rescope_followup_uses_reasoner_and_preserves_count_shape() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    reasoner_calls = 0

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        nonlocal reasoner_calls
        reasoner_calls += 1
        return QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_replace_scope_yesterday",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 18), end=date(2026, 3, 18), granularity="day"),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert updates["continuation_type"] == "time_delta"
    assert updates["continuation_delta_type"] == "time"
    assert updates["current_page"] == 0
    assert updates["show_expanded"] is False
    assert reasoner_calls == 1


@pytest.mark.asyncio
async def test_direct_time_rescope_followup_overrides_wrong_reasoner_time_range() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_wrongly_kept_today_range",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            delta_type="time",
            time_range=TimeRange(start=today, end=today, granularity="day"),
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert updates["continuation_type"] == "time_delta"
    assert updates["continuation_delta_type"] == "time"


@pytest.mark.asyncio
async def test_direct_time_rescope_followup_recovers_from_non_time_continuation_label() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            confidence=0.93,
            reason="llm_mislabeled_yesterday_as_filter_delta",
            continuation_type="filter_delta",
            followup_intent="refine_existing",
            delta_type="filter",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "What about yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert updates["continuation_type"] == "time_delta"
    assert updates["continuation_delta_type"] == "time"


@pytest.mark.asyncio
async def test_direct_time_rescope_correction_recovers_from_fresh_query_label() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    session_query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=today, end=today),
        aggregation=Aggregation(type="count"),
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="fresh_query",
            confidence=0.48,
            reason="llm_mislabeled_correction_as_fresh_query",
            continuation_type="unclear",
            followup_intent="none",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "I meant yesterday", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 0,
            "show_expanded": False,
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "count"
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert updates["continuation_type"] == "time_delta"
    assert updates["continuation_delta_type"] == "time"


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 2,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 16)
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 3,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == today
    assert query_request.time_end == today
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["Mum"]
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
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
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

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert query_request.result_limit == 1
    assert query_request.result_reference == "latest"


@pytest.mark.asyncio
async def test_single_item_grounded_ask_clarify_recovers_to_yesterday_time_rescope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 3, 19)
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.nodes.extraction.logger.info", _capture)
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
            "query_request": session_contract.model_dump(),
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
    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    assert query_request.result_limit == 1
    assert query_request.result_reference == "latest"
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
async def test_single_item_next_fact_followup_answers_from_semantic_decision() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 4, 13)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 4, 1), end=date(2026, 4, 10)),
        filters=Filters(transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
        answer_fact_field="counterparty",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_action="answer_fact",
            drill_down_index=1,
            fact_field="recipient",
            confidence=0.94,
            reason="semantic_next_fact_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "Then who next?", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "The last person you sent money to was Mum.",
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Transfer to Mum",
                        amount=50000.0,
                        date=date(2026, 4, 10),
                        metadata={"type": "debit", "recipient_name": "Mum", "bank_name": "Zenith Bank"},
                    ).model_dump(mode="json")
                ],
                "surface_view": {
                    "mode": "direct_answer",
                    "context": {"type": "single_transaction"},
                },
            },
            "cached_transactions": [
                {
                    "id": "txn_last",
                    "narration": "Transfer to Mum",
                    "amount": 50000.0,
                    "date": "2026-04-10",
                    "type": "debit",
                    "transaction_type": "debit",
                    "recipient_name": "Mum",
                    "recipient_bank_name": "Zenith Bank",
                    "counterparty": "Mum",
                },
                {
                    "id": "txn_prev",
                    "narration": "Transfer to Tolu",
                    "amount": 25000.0,
                    "date": "2026-04-08",
                    "type": "debit",
                    "transaction_type": "debit",
                    "recipient_name": "Tolu",
                    "recipient_bank_name": "First Bank",
                    "counterparty": "Tolu",
                },
            ],
            "current_page": 0,
            "show_expanded": False,
        },
    )

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert "Tolu" in updates["response"]
    assert updates["selected_item_index"] == 1
    assert updates["_query_session_transition"] == "answer_fact_active_result"


@pytest.mark.asyncio
async def test_single_item_current_fact_followup_answers_selected_item_from_semantic_decision() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 4, 13)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 4, 1), end=date(2026, 4, 10)),
        filters=Filters(transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
        answer_fact_field="amount",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_action="answer_fact",
            drill_down_index=1,
            fact_field="amount",
            confidence=0.94,
            reason="semantic_current_fact_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "How much?", "today": today, "language": "en"},
        {
            "session_active": True,
            "selected_item_index": 1,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "Looks like that went to Dad.",
                "items": [
                    QueryResultItem(
                        id="txn_last",
                        description="Transfer to Mum",
                        amount=50000.0,
                        date=date(2026, 4, 10),
                        metadata={"type": "debit", "recipient_name": "Mum", "bank_name": "Zenith Bank"},
                    ).model_dump(mode="json"),
                    QueryResultItem(
                        id="txn_prev",
                        description="Transfer to Dad",
                        amount=25000.0,
                        date=date(2026, 4, 8),
                        metadata={"type": "debit", "recipient_name": "Dad", "bank_name": "First Bank"},
                    ).model_dump(mode="json"),
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

    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert "₦25,000" in updates["response"]
    assert updates["selected_item_index"] == 1
    assert updates["_query_session_transition"] == "answer_fact_active_result"


@pytest.mark.asyncio
async def test_direct_fact_counterparty_answer_when_was_that_followup_uses_focused_item() -> None:
    step = ExtractionStep(_DummyLLM())
    today = date(2026, 6, 28)
    session_query = _query_ir(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_range=TimeRange(start=date(2026, 6, 1), end=today),
        filters=Filters(transaction_type="credit"),
        result_limit=1,
        answer_fact_field="counterparty",
    )
    session_contract = _contract(session_query)

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="drill_down",
            followup_intent="none",
            drill_down_action="answer_fact",
            fact_field="date",
            confidence=0.96,
            reason="semantic_direct_fact_referential_date_followup",
        )

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]

    updates = await step._handle_continuation(
        {"message": "When was that", "today": today, "language": "en"},
        {
            "session_active": True,
            "query_request": session_contract.model_dump(),
            "query_result": {
                "summary_text": "That was with Acme Corp.",
                "items": [
                    QueryResultItem(
                        id="txn_acme",
                        description="Acme Corp",
                        amount=950000.0,
                        date=today,
                        metadata={
                            "date": "2026-06-28",
                            "type": "credit",
                            "transaction_type": "credit",
                            "counterparty": "Acme Corp",
                            "recipient_name": "Acme Corp",
                            "bank_name": "Zenith Bank",
                        },
                    ).model_dump(mode="json")
                ],
                "surface_view": {
                    "mode": "direct_answer",
                    "lead_text": "That was with Acme Corp.",
                    "items": [
                        SurfaceItemView(
                            id="txn_acme",
                            label="Acme Corp",
                            amount=950000.0,
                            payload=SelectionPayload(
                                selection_kind="transaction",
                                entity_type="transaction",
                                entity_id="txn_acme",
                                label="Acme Corp",
                                fact_capabilities=["date", "amount", "bank", "reference"],
                            ),
                            metadata={
                                "date": "2026-06-28",
                                "type": "credit",
                                "counterparty": "Acme Corp",
                                "bank_name": "Zenith Bank",
                            },
                        ).model_dump(mode="json")
                    ],
                    "context": {
                        "type": "single_transaction",
                        "focus_type": "transaction",
                        "selected_item_id": "txn_acme",
                    },
                },
            },
            "current_page": 0,
            "show_expanded": False,
        },
    )

    assert updates["flow_state"] == "executing"
    assert updates["continuation_type"] == "drill_down"
    assert updates["selected_item_id"] == "txn_acme"
    assert updates["selected_item_index"] == 0
    assert updates["drill_down_action"] == "answer_fact"
    assert updates["fact_field"] == "date"


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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 4,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 2, 1)
    assert query_request.time_end == date(2026, 2, 28)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
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
                intent=QueryIntent.TRANSACTION_LIST,
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
            "query_request": session_contract.model_dump(),
            "query_result": {"items": []},
            "current_page": 1,
            "show_expanded": True,
        },
    )

    query_request = updates["query_request"]
    assert query_request.time_start == date(2026, 3, 9)
    assert query_request.time_end == date(2026, 3, 15)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
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
            "query_request": session_contract.model_dump(),
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
            "query_request": session_contract.model_dump(),
            "query_result": query_result.model_dump(mode="json"),
        },
    )

    query_request = updates["query_request"]
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.filters is not None
    assert query_request.filters.account_filter == "First Bank"
    assert query_request.aggregation is None
