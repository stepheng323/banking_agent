"""Regression tests for locale-safe query formatter behavior."""

from datetime import date, timedelta

import pytest

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryExecutionPlan,
    QueryIntent,
    QueryIntentSpec,
    QueryIR,
    QueryResult,
    QueryResultItem,
    TimeRange,
    build_query_execution_plan_from_fields,
    derive_query_intent_spec_from_fields,
)
from banking.transactions.query.presentation.formatter import QueryFormatter
from banking.transactions.query.presentation.surface_builder import build_surface_view
from banking.transactions.query.services.answers.strategy import select_answer_strategy
from banking.transactions.query.utils.timezone import lagos_today


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = lagos_today()
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


def _query_contract(query: QueryIR) -> QueryExecutionContract:
    intent_spec: QueryIntentSpec = query.intent_spec or derive_query_intent_spec_from_fields(
        intent=query.intent,
        filters=query.filters,
        aggregation=query.aggregation,
        answer_fact_field=query.answer_fact_field,
    )
    fallback_day = lagos_today()
    execution_plan: QueryExecutionPlan = build_query_execution_plan_from_fields(
        intent_spec=intent_spec,
        time_range=query.time_range,
        filters=query.filters,
        aggregation=query.aggregation,
        result_limit=query.result_limit,
        result_reference=query.result_reference,
    )
    fallback_day = lagos_today()
    return QueryExecutionContract(
        intent=query.intent,
        time_start=fallback_day,
        time_end=fallback_day,
        filters=query.filters.model_copy(deep=True) if query.filters is not None else None,
        aggregation=query.aggregation.model_copy(deep=True) if query.aggregation is not None else None,
        accounts_scope=query.accounts_scope,
        account_name=query.account_name,
        amount_check=query.amount_check,
        item_name=query.item_name,
        analysis_type=query.analysis_type,
        result_limit=query.result_limit,
        result_reference=query.result_reference,
        answer_fact_field=query.answer_fact_field,
        intent_spec=intent_spec,
        execution_plan=execution_plan,
    )


def _contract_without_time(
    *,
    intent: QueryIntent,
    filters: Filters | None = None,
    aggregation: Aggregation | None = None,
) -> QueryExecutionContract:
    fallback_day = lagos_today()
    return QueryExecutionContract(
        intent=intent,
        time_start=fallback_day,
        time_end=fallback_day,
        filters=filters.model_copy(deep=True) if filters is not None else None,
        aggregation=aggregation.model_copy(deep=True) if aggregation is not None else None,
    )


def _assert_not_structural_user_copy(response: str) -> None:
    assert "accounts:" not in response
    assert "showing:" not in response
    assert "total:" not in response


def test_formatter_returns_summary_for_summary_surface() -> None:
    result = QueryResult(
        summary_text="Akopọ inawo rẹ",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer",
                amount=1000,
                date=date.today(),
                metadata={"type": "debit"},
            )
        ],
        surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context={"view": "summary"}),
    )

    assert QueryFormatter.format(result, locale="yo") == "Akopọ inawo rẹ"


def test_formatter_renders_beneficiary_summary_from_presentation_plan() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="*Recipients I sent over ₦20,000 to* — Mar 14 – Mar 27",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Cowrywise",
                amount=150000,
                date=date(2026, 3, 27),
                metadata={"count": 2, "recipient_name": "Cowrywise"},
            ),
            QueryResultItem(
                id="bene_2",
                description="Tolu",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"count": 1, "recipient_name": "Tolu"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="debit"),
                aggregation=Aggregation(type="sum", sort_by="amount"),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="bene_1",
                    label="Cowrywise",
                    amount=150000,
                    count=2,
                    payload=SelectionPayload(
                        selection_kind="beneficiary",
                        entity_type="beneficiary",
                        entity_id="bene_1",
                        label="Cowrywise",
                    ),
                ),
                SurfaceItemView(
                    id="bene_2",
                    label="Tolu",
                    amount=50000,
                    count=1,
                    payload=SelectionPayload(
                        selection_kind="beneficiary",
                        entity_type="beneficiary",
                        entity_id="bene_2",
                        label="Tolu",
                    ),
                ),
            ],
            context={"surface_type": "summary", "view": "beneficiary_summary"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.startswith("You sent money to 2 recipients this month.")
    assert "Your top recipients:" in response
    assert "Cowrywise · ₦150,000 · 2 transfers" in response
    assert "Tolu · ₦50,000 · 1 transfer" in response
    assert "2x" not in response
    assert "Say a name to see the matching transactions." in response


def test_formatter_renders_credit_beneficiary_summary_as_top_senders() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="*Top Senders* — Mar 01 – Mar 27",
        items=[
            QueryResultItem(
                id="bene_1",
                description="FATIMA ZAHRA MUSA",
                amount=75000,
                date=date(2026, 3, 27),
                metadata={"count": 4, "recipient_name": "FATIMA ZAHRA MUSA"},
            ),
            QueryResultItem(
                id="bene_2",
                description="Slot Systems",
                amount=450000,
                date=date(2026, 3, 24),
                metadata={"count": 1, "recipient_name": "Slot Systems"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="credit"),
                aggregation=Aggregation(type="sum", sort_by="amount"),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="bene_1",
                    label="FATIMA ZAHRA MUSA",
                    amount=75000,
                    count=4,
                    payload=SelectionPayload(
                        selection_kind="beneficiary",
                        entity_type="beneficiary",
                        entity_id="bene_1",
                        label="FATIMA ZAHRA MUSA",
                    ),
                ),
                SurfaceItemView(
                    id="bene_2",
                    label="Slot Systems",
                    amount=450000,
                    count=1,
                    payload=SelectionPayload(
                        selection_kind="beneficiary",
                        entity_type="beneficiary",
                        entity_id="bene_2",
                        label="Slot Systems",
                    ),
                ),
            ],
            context={"surface_type": "summary", "view": "beneficiary_summary"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.startswith("You received money from 2 senders this month.")
    assert "Your top senders:" in response
    assert "Top Recipients" not in response
    assert "Fatima Zahra Musa · ₦75,000 · 4 transfers" in response


def test_formatter_renders_singular_beneficiary_ranking_without_full_list() -> None:
    today = lagos_today()
    result = QueryResult(
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
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="credit"),
                aggregation=Aggregation(type="sum", sort_by="amount"),
                result_limit=1,
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
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
                    ),
                ),
                SurfaceItemView(
                    id="bene_2",
                    label="Techcorp Nigeria Ltd",
                    amount=850000,
                    count=1,
                    payload=SelectionPayload(
                        selection_kind="beneficiary",
                        entity_type="beneficiary",
                        entity_id="bene_2",
                        label="Techcorp Nigeria Ltd",
                    ),
                ),
            ],
            context={"surface_type": "summary", "view": "beneficiary_summary"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "Acme Corp sent you the most this month: ₦950,000. (1 transfer)"
    assert "Your top senders:" not in response
    assert "Techcorp Nigeria Ltd" not in response
    assert "Say a name" not in response


def test_formatter_builds_beneficiary_surface_with_extended_fact_capabilities() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="*Top Senders* — This Month",
        items=[
            QueryResultItem(
                id="bene_1",
                description="Acme Corp",
                amount=950000,
                date=today,
                metadata={"count": 1, "recipient_name": "Acme Corp"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="credit"),
                aggregation=Aggregation(type="sum", sort_by="amount"),
                result_limit=1,
            )
        ),
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.startswith("Acme Corp sent you the most this month: ₦950,000.")
    assert result.surface_view is None


def test_singular_beneficiary_ranking_builds_focused_surface_for_followups() -> None:
    today = lagos_today()
    result = QueryResult(
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
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="credit"),
                aggregation=Aggregation(type="sum", sort_by="amount"),
                result_limit=1,
            )
        ),
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )

    surface = build_surface_view(result)

    assert surface is not None
    assert surface.mode == SurfaceViewMode.DIRECT_ANSWER
    assert len(surface.items) == 1
    assert surface.items[0].label == "Acme Corp"
    assert surface.items[0].payload.filters_patch == {"counterparty": ["Acme Corp"]}
    assert "reference" in surface.items[0].payload.fact_capabilities
    assert surface.context["focus_type"] == "beneficiary"
    assert len(result.items) == 2


def test_singular_category_breakdown_builds_focused_group_bucket_surface_for_followups() -> None:
    today = lagos_today()
    result = QueryResult(
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
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="debit"),
                aggregation=Aggregation(type="breakdown", group_by="category", sort_by="amount"),
                result_limit=1,
            )
        ),
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )

    surface = build_surface_view(result)

    assert surface is not None
    assert surface.mode == SurfaceViewMode.DIRECT_ANSWER
    assert len(surface.items) == 1
    assert surface.context["focus_type"] == "group_bucket"
    assert surface.items[0].payload.selection_kind == "group_bucket"
    assert surface.items[0].payload.filters_patch == {"category": ["shopping"]}
    assert "reference" in surface.items[0].payload.fact_capabilities
    assert len(result.items) == 2


def test_singular_account_breakdown_builds_focused_account_surface_for_followups() -> None:
    today = lagos_today()
    result = QueryResult(
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
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=today.replace(day=1), end=today, granularity="month"),
                filters=Filters(transaction_type="debit"),
                aggregation=Aggregation(type="breakdown", group_by="account", sort_by="amount"),
                result_limit=1,
            )
        ),
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
    )

    surface = build_surface_view(result)

    assert surface is not None
    assert surface.mode == SurfaceViewMode.DIRECT_ANSWER
    assert len(surface.items) == 1
    assert surface.context["focus_type"] == "account"
    assert surface.items[0].payload.selection_kind == "account"
    assert surface.items[0].payload.filters_patch == {"account_filter": "GTBank"}
    assert "bank" in surface.items[0].payload.fact_capabilities


def test_formatter_returns_summary_when_no_items() -> None:
    result = QueryResult(summary_text="Ko si transaction to baamu.")

    assert QueryFormatter.format(result, locale="yo") == "Ko si transaction to baamu."


def test_formatter_uses_summary_list_plan_for_summary_only_results() -> None:
    result = QueryResult(
        summary_text="You can afford ₦20,000 right now.",
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        query_contract=_query_contract(_query_ir(intent=QueryIntent.AFFORDABILITY)),
    )

    assert QueryFormatter.format(result, locale="en") == "You can afford ₦20,000 right now."


def test_formatter_uses_summary_list_plan_for_account_summary_results() -> None:
    result = QueryResult(
        summary_text="accounts:2|total:₦200,000",
        items=[
            QueryResultItem(
                id="1",
                description="Zenith Bank",
                amount=120000,
                date=date(2026, 3, 28),
            ),
            QueryResultItem(
                id="2",
                description="First Bank",
                amount=80000,
                date=date(2026, 3, 28),
            ),
        ],
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="1",
                    label="Zenith Bank",
                    amount=120000,
                    payload=SelectionPayload(
                        selection_kind="account",
                        entity_type="account",
                        entity_id="1",
                        label="Zenith Bank",
                    ),
                ),
                SurfaceItemView(
                    id="2",
                    label="First Bank",
                    amount=80000,
                    payload=SelectionPayload(
                        selection_kind="account",
                        entity_type="account",
                        entity_id="2",
                        label="First Bank",
                    ),
                ),
            ],
            context={"surface_type": "summary", "view": "accounts"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "\n".join(
        [
            "*Your Accounts*",
            "",
            "₦120,000 — Zenith Bank",
            "₦80,000 — First Bank",
            "",
            "*Total: ₦200,000*",
        ]
    )


def test_formatter_uses_typed_breakdown_heading_from_query_scope() -> None:
    result = QueryResult(
        summary_text="Ìtúpalẹ̀ nípasẹ̀ merchant",
        items=[
            QueryResultItem(
                id="1",
                description="food",
                amount=2000,
                date=date.today(),
                metadata={"count": 2},
            )
        ],
        query_contract=_contract_without_time(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=Aggregation(type="breakdown", group_by="merchant"),
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="1",
                    label="food",
                    amount=2000,
                    count=2,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="1",
                        label="food",
                        group_by="merchant",
                        group_key="food",
                        filters_patch={"counterparty": ["food"]},
                    ),
                )
            ],
            context={"surface_type": "breakdown", "group_by": "merchant"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "Here is your 📊 Breakdown by merchant:"
    assert "Food" in response


def test_formatter_uses_rank_metadata_without_english_summary() -> None:
    result = QueryResult(
        summary_text="Akopọ ipo inawo",
        items=[
            QueryResultItem(
                id="r1",
                description="Groceries",
                amount=5000,
                date=date.today(),
                metadata={"rank": 1, "type": "debit"},
            ),
            QueryResultItem(
                id="r2",
                description="Fuel",
                amount=3000,
                date=date.today(),
                metadata={"rank": 2, "type": "debit"},
            ),
        ],
    )

    response = QueryFormatter.format(result, locale="yo")
    assert "🏆" in response
    assert "1." in response


def test_formatter_uses_shared_plan_for_ranked_results() -> None:
    result = QueryResult(
        summary_text="Top 2 Largest expenses",
        items=[
            QueryResultItem(
                id="r1",
                description="Rent",
                amount=250000,
                date=date(2026, 3, 21),
                metadata={"rank": 1, "type": "debit", "bank_name": "Zenith Bank"},
            ),
            QueryResultItem(
                id="r2",
                description="School Fees",
                amount=180000,
                date=date(2026, 3, 19),
                metadata={"rank": 2, "type": "debit"},
            ),
        ],
        surface_view=SurfaceView(
            mode=SurfaceViewMode.TRANSACTION_LIST,
            items=[
                SurfaceItemView(
                    id="r1",
                    label="Rent",
                    amount=250000,
                    payload=SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id="r1",
                        label="Rent",
                    ),
                    metadata={"rank": 1, "bank_name": "Zenith Bank"},
                ),
                SurfaceItemView(
                    id="r2",
                    label="School Fees",
                    amount=180000,
                    payload=SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id="r2",
                        label="School Fees",
                    ),
                    metadata={"rank": 2},
                ),
            ],
            context={"type": "largest"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "Here are your 🏆 Top 2 Largest expenses:"
    assert "1. *₦250,000* — Rent Mar 21 _(Zenith Bank)_" in response
    assert "2. *₦180,000* — School Fees Mar 19" in response


def test_formatter_no_results_with_type_for_today() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(transaction_type="credit"),
                time_range=TimeRange(start=today, end=today),
            )
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no credit transactions today."


def test_formatter_no_results_with_type_for_period() -> None:
    today = date.today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=today - timedelta(days=7), end=today - timedelta(days=1)),
            )
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no debit transactions during that period."


def test_formatter_account_scoped_no_results_uses_normalized_bank_copy() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="accounts:1|showing:1-0|total:0",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(account_filter="gtb"),
                time_range=TimeRange(start=today - timedelta(days=today.weekday()), end=today),
            )
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def test_formatter_no_results_without_type_for_yesterday_uses_direct_fact() -> None:
    yesterday = lagos_today() - timedelta(days=1)
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_range=TimeRange(start=yesterday, end=yesterday),
                result_limit=1,
                result_reference="latest",
            )
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no transactions yesterday."


def test_formatter_search_shaped_no_results_for_yesterday_stays_generic() -> None:
    yesterday = lagos_today() - timedelta(days=1)
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(merchant=["mum"]),
                time_range=TimeRange(start=yesterday, end=yesterday),
            )
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def test_formatter_no_results_without_type_uses_generic_message() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_contract_without_time(intent=QueryIntent.TRANSACTION_LIST),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def _sample_list_result(query_snapshot: QueryIR) -> QueryResult:
    return QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Ada",
                amount=2000,
                date=date.today(),
                metadata={"type": "debit", "bank_name": "Zenith"},
            ),
            QueryResultItem(
                id="tx2",
                description="Salary",
                amount=10000,
                date=date.today(),
                metadata={"type": "credit", "bank_name": "Zenith"},
            ),
        ],
        query_contract=_query_contract(query_snapshot),
    )


def test_formatter_heading_uses_credit_context() -> None:
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
        )
    )
    result.query_contract = _contract_without_time(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=Filters(transaction_type="credit"),
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "I found 2 credit transactions today."


def test_formatter_preserves_paginated_credit_list_shape_for_single_remaining_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = date(2026, 4, 15)
    monkeypatch.setattr(
        "banking.transactions.query.presentation.transaction_list_plan.lagos_today",
        lambda: today,
    )
    result = QueryResult(
        summary_text="accounts:1|showing:6-6|total:6",
        items=[
            QueryResultItem(
                id="tx6",
                description="Transfer from JOHNSON MARY - Refund",
                amount=35000,
                date=today.replace(day=1),
                metadata={"type": "credit", "bank_name": "Zenith Bank"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(transaction_type="credit"),
                time_range=TimeRange(start=today.replace(day=1), end=today),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.TRANSACTION_LIST,
            context={"type": "transaction_list", "count": 1, "total_results": 6, "has_more": False},
        ),
    )

    response = QueryFormatter.format(result, current_page=1, locale="en")

    assert response.splitlines()[0] == "Here is 1 more."
    assert "Your last credit transaction was:" not in response
    assert "₦35,000" in response
    assert "Showing 6-6 of 6" not in response


def test_formatter_heading_uses_category_spending_for_debit() -> None:
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit", category=["food"]),
        )
    )
    result.query_contract = _contract_without_time(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=Filters(transaction_type="debit", category=["food"]),
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "I found 2 Food transactions today."


def test_formatter_heading_includes_amount_scope_for_transaction_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = date(2026, 4, 15)
    monkeypatch.setattr(
        "banking.transactions.query.presentation.transaction_list_plan.lagos_today",
        lambda: today,
    )
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit", min_amount=20000),
            time_range=TimeRange(start=today.replace(day=1), end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "I found 2 debit transactions this month."


def test_formatter_direct_answer_uses_answer_strategy_without_transaction_card() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"type": "debit", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="date",
            )
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(
            primary_text="You paid Mum on March 24, 2026.",
            secondary_text="₦50,000 • Zenith Bank",
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "You paid Mum on March 24, 2026.\n\n₦50,000 • Zenith Bank"
    assert "Transaction Details" not in response


def test_direct_analytics_answer_builds_summary_scope_surface_without_evidence_focus() -> None:
    result = QueryResult(
        summary_text="You spent ₦75,000 this month, across 2 transactions.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 6, 24),
                metadata={"type": "debit", "counterparty": "Mum"},
            ),
            QueryResultItem(
                id="tx2",
                description="Transfer to Dad",
                amount=25000,
                date=date(2026, 6, 23),
                metadata={"type": "debit", "counterparty": "Dad"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="debit"),
                aggregation=Aggregation(type="sum"),
                time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 29)),
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")
    surface = build_surface_view(selected)
    response = QueryFormatter.format(selected, locale="en")

    assert response == "You spent ₦75,000 this month, across 2 transactions."
    assert surface is not None
    assert surface.mode == SurfaceViewMode.DIRECT_ANSWER
    assert surface.context["focus_type"] == "summary_scope"
    assert surface.context["type"] == "summary_scope"
    assert len(surface.items) == 1
    assert surface.items[0].payload.selection_kind == "summary_scope"
    assert surface.items[0].id == "summary_scope"


def test_formatter_fact_no_results_prefers_natural_copy_under_direct_answer() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="date",
            )
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "I couldn't find a payment to Mum in that period."


def test_formatter_unscoped_latest_fact_no_results_drops_bounded_period_copy() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=today - timedelta(days=180), end=today),
                answer_fact_field="date",
                result_reference="latest",
            )
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "I couldn't find a payment to Mum."


def test_formatter_heading_appends_account_and_today_suffix() -> None:
    today = lagos_today()
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(account_filter="Zenith"),
            time_range=TimeRange(start=today, end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "I found 2 Zenith Bank transactions today."
    assert "Today · 2 found" not in response


def test_formatter_account_heading_normalizes_alias_and_labels_default_window() -> None:
    today = lagos_today()
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(account_filter="gtb"),
            time_range=TimeRange(start=today - timedelta(days=30), end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "I found 2 GTBank transactions in the last 30 days."
    assert "Last 30 days · 2 found" not in response


def test_formatter_one_account_result_uses_conversational_lead_without_pagination() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Tolu Adebayo",
                amount=15000,
                date=date(2026, 6, 15),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "counterparty": "Tolu Adebayo",
                    "bank_name": "GTBank",
                    "recipient_account": "0001",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(account_filter="gtb"),
                time_range=TimeRange(start=date(2026, 6, 15), end=date(2026, 6, 21)),
            )
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "I found one GTBank transaction for Jun 15–Jun 21."
    assert "Showing 1-1 of 1" not in response


def test_formatter_paginated_transaction_list_uses_natural_continuation_copy() -> None:
    items = [
        QueryResultItem(
            id=f"tx{idx}",
            description=f"Transfer to Person {idx}",
            amount=1000 * idx,
            date=date(2026, 6, 11),
            metadata={"type": "debit", "transaction_type": "transfer", "counterparty": f"Person {idx}"},
        )
        for idx in range(1, 6)
    ]
    result = QueryResult(
        summary_text="accounts:1|showing:1-5|total:9",
        items=items,
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 6, 11), end=date(2026, 6, 11)),
            )
        ),
    )

    response = QueryFormatter.format(result, has_more=True, locale="en")

    assert response.splitlines()[0] == "I found 9 transactions for Jun 11. Here are the first 5."
    assert "More for next page" in response
    assert "Showing 1-5 of 9" not in response


def test_formatter_groups_transaction_rows_under_one_date_heading() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Ada",
                amount=2000,
                date=date(2026, 6, 11),
                metadata={"type": "debit", "transaction_type": "transfer", "counterparty": "Ada"},
            ),
            QueryResultItem(
                id="tx2",
                description="Transfer to Mum",
                amount=5000,
                date=date(2026, 6, 11),
                metadata={"type": "debit", "transaction_type": "transfer", "counterparty": "Mum"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 6, 11), end=date(2026, 6, 11)),
            )
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "I found 2 debit transactions for Jun 11."
    assert response.count("*Jun 11*") == 1
    assert "• ₦2,000" in response or "• ₦5,000" in response
    assert "\n• ₦5,000 — Sent to Mum" in response


def test_formatter_heading_single_day_past_range_is_not_labeled_today() -> None:
    today = date.today()
    past_day = today - timedelta(days=1)
    result = _sample_list_result(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=past_day, end=past_day),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    heading = response.splitlines()[0]
    past_label = past_day.strftime("%b %d").replace(" 0", " ")
    assert heading == f"I found 2 transactions for {past_label}."
    assert "Today" not in heading


def test_formatter_single_item_fact_query_leads_with_requested_date() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="txn_b01",
                description="Netflix Monthly Subscription",
                amount=6500,
                date=date(2026, 3, 21),
                metadata={"type": "debit", "bank_name": "First Bank", "counterparty": "Netflix"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", merchant=["netflix"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21)),
                result_limit=1,
                result_reference="latest",
                answer_fact_field="date",
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context={"type": "single_transaction"},
        ),
    )

    response = QueryFormatter.format(select_answer_strategy(result, locale="en"), locale="en")
    lines = response.splitlines()

    assert lines[0] == "The last time you paid Netflix was on March 21, 2026."
    assert "Your last debit transaction was:" not in response


def test_formatter_single_item_fact_query_leads_with_counterparty() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="txn_c01",
                description="Transfer from JOHNSON MARY - Refund",
                amount=35000,
                date=date(2026, 3, 21),
                metadata={"type": "credit", "bank_name": "Zenith Bank", "counterparty": "Johnson Mary"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="credit"),
                time_range=TimeRange(start=date(2026, 3, 15), end=date(2026, 3, 21)),
                answer_fact_field="counterparty",
                result_reference="latest",
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context={"type": "single_transaction"},
        ),
    )

    response = QueryFormatter.format(select_answer_strategy(result, locale="en"), locale="en")
    lines = response.splitlines()

    assert lines[0] == "The last person who sent you money was Johnson Mary."
    assert lines[2] == "₦35,000 • Mar 21 • Zenith Bank"


def test_formatter_uses_shared_plan_for_single_transfer_detail_surface() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="txn_t01",
                description="Transfer to Ada",
                amount=20000,
                date=date(2026, 3, 21),
                metadata={
                    "type": "debit",
                    "bank_name": "Zenith Bank",
                    "transaction_type": "transfer",
                    "status": "success",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21)),
                result_reference="latest",
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id="txn_t01",
                    label="Transfer to Ada",
                    amount=20000,
                    payload=SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id="txn_t01",
                        label="Transfer to Ada",
                    ),
                    metadata={"type": "debit", "transaction_type": "transfer"},
                )
            ],
            context={"type": "single_transaction"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert "*Your last debit transaction was:*" in response
    assert "*Amount:* ₦20,000.00" in response
    assert "*Bank:* Zenith Bank" in response
    assert render_message("query.format.transfer_reply_hint", "en") in response


def test_formatter_direct_answer_uses_localized_reply_in_yoruba() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"type": "debit", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="date",
            )
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(
            primary_text="O san owo si Mum ni March 24, 2026.",
            secondary_text="₦50,000 • Zenith Bank",
        ),
    )

    response = QueryFormatter.format(result, locale="yo")

    assert response == "O san owo si Mum ni March 24, 2026.\n\n₦50,000 • Zenith Bank"
    assert "*Date:*" not in response


def test_formatter_single_item_detail_suppresses_synthetic_reference() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="0",
                description="Mum",
                amount=50000,
                date=date(2026, 3, 28),
                metadata={"type": "debit", "bank_name": "Zenith Bank"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 28)),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id="0",
                    label="Mum",
                    amount=50000,
                    payload=SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id="0",
                        label="Mum",
                    ),
                )
            ],
            context={"type": "single_transaction", "selected_item_id": "0"},
        ),
    )

    response = QueryFormatter.format(result, show_expanded=True, locale="en")

    assert "*Ref:*" not in response


def test_formatter_no_results_does_not_leak_structural_account_summary_for_transaction_list() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="accounts:2|showing:1-0|total:0",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=today, end=today),
            )
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    _assert_not_structural_user_copy(response)
    assert response == "You had no transactions today."


def test_formatter_no_results_accepts_zero_zero_structural_summary_for_transaction_list() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="accounts:2|showing:0-0|total:0",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=today, end=today),
            )
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    _assert_not_structural_user_copy(response)
    assert response == "You had no transactions today."


def test_formatter_no_results_without_contract_does_not_leak_structural_summary() -> None:
    result = QueryResult(
        summary_text="accounts:2|showing:1-0|total:0",
        items=[],
    )

    response = QueryFormatter.format(result, locale="en")

    _assert_not_structural_user_copy(response)
    assert response == "No matching transactions found for your search."


def test_formatter_account_breakdown_preserves_account_labels_and_generic_total() -> None:
    result = QueryResult(
        summary_text="Breakdown by account",
        items=[
            QueryResultItem(
                id="1",
                description="Zenith Bank",
                amount=-120000,
                date=date(2026, 3, 21),
                metadata={"count": 2},
            ),
            QueryResultItem(
                id="2",
                description="First Bank",
                amount=-80000,
                date=date(2026, 3, 21),
                metadata={"count": 1},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="breakdown", group_by="account"),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="1",
                    label="Zenith Bank",
                    amount=-120000,
                    count=2,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="1",
                        label="Zenith Bank",
                        group_by="account",
                        group_key="Zenith Bank",
                        filters_patch={"account_filter": "Zenith Bank"},
                    ),
                ),
                SurfaceItemView(
                    id="2",
                    label="First Bank",
                    amount=-80000,
                    count=1,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="2",
                        label="First Bank",
                        group_by="account",
                        group_key="First Bank",
                        filters_patch={"account_filter": "First Bank"},
                    ),
                ),
            ],
            context={"surface_type": "breakdown", "group_by": "account"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert "Zenith Bank" in response
    assert "First Bank" in response
    assert "*Total: ₦200,000*" in response
    assert "Total spent this month" not in response


def test_formatter_breakdown_heading_includes_amount_scope_and_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = date(2026, 4, 15)
    monkeypatch.setattr(
        "banking.transactions.query.presentation.scope.lagos_today",
        lambda: today,
    )
    result = QueryResult(
        summary_text="Breakdown by account",
        items=[
            QueryResultItem(
                id="1",
                description="Zenith Bank",
                amount=-120000,
                date=today,
                metadata={"count": 2},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="debit", min_amount=20000),
                aggregation=Aggregation(type="breakdown", group_by="account"),
                time_range=TimeRange(start=today.replace(day=1), end=today),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="1",
                    label="Zenith Bank",
                    amount=-120000,
                    count=2,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="1",
                        label="Zenith Bank",
                        group_by="account",
                        group_key="Zenith Bank",
                        filters_patch={"account_filter": "Zenith Bank"},
                    ),
                )
            ],
            context={"surface_type": "breakdown", "group_by": "account"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "Here is your 📊 Spending by account — Over ₦20,000 — This Month:"


def test_formatter_credit_account_breakdown_uses_income_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = date(2026, 4, 15)
    monkeypatch.setattr(
        "banking.transactions.query.presentation.scope.lagos_today",
        lambda: today,
    )
    result = QueryResult(
        summary_text="Breakdown by account",
        items=[
            QueryResultItem(
                id="1",
                description="Zenith Bank",
                amount=950000,
                date=today,
                metadata={"count": 1},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="credit"),
                aggregation=Aggregation(type="breakdown", group_by="account"),
                time_range=TimeRange(start=today.replace(day=1), end=today),
            )
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id="1",
                    label="Zenith Bank",
                    amount=950000,
                    count=1,
                    payload=SelectionPayload(
                        selection_kind="group_bucket",
                        entity_type="group_bucket",
                        entity_id="1",
                        label="Zenith Bank",
                        group_by="account",
                        group_key="Zenith Bank",
                        filters_patch={"account_filter": "Zenith Bank"},
                    ),
                )
            ],
            context={"surface_type": "breakdown", "group_by": "account"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    first_line = response.splitlines()[0]
    assert first_line == "Here is your 📊 Money came in by account — This Month:"
    assert "Spending by account" not in response
