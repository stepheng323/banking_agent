"""Regression tests for locale-safe query formatter behavior."""

from datetime import date, timedelta

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Filters,
    NormalizedQuery,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.formatter import QueryFormatter
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.shared.query_contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from shared.i18n import render_message


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

    assert "Cowrywise • ₦150,000 (2x)" in response
    assert "Tolu • ₦50,000 (1x)" in response
    assert "Reply with a name to see those transactions" in response


def test_formatter_returns_summary_when_no_items() -> None:
    result = QueryResult(summary_text="Ko si transaction to baamu.")

    assert QueryFormatter.format(result, locale="yo") == "Ko si transaction to baamu."


def test_formatter_uses_summary_list_plan_for_summary_only_results() -> None:
    result = QueryResult(
        summary_text="You can afford ₦20,000 right now.",
        answer_strategy=QueryAnswerStrategy.SUMMARY_LIST,
        query_snapshot=NormalizedQuery(intent=QueryIntent.AFFORDABILITY),
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
            "💰 *Your Accounts*",
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
        query_snapshot=NormalizedQuery(
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
    assert response.splitlines()[0] == "Breakdown by merchant"
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

    assert response.splitlines()[0] == "🏆 *Top 2 Largest expenses*"
    assert "1. *₦250,000* — Rent Mar 21 _(Zenith Bank)_" in response
    assert "2. *₦180,000* — School Fees Mar 19" in response


def test_formatter_no_results_with_type_for_today() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
            time_range=TimeRange(start=today, end=today),
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no credit transactions today."


def test_formatter_no_results_with_type_for_period() -> None:
    today = date.today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit"),
            time_range=TimeRange(start=today - timedelta(days=7), end=today - timedelta(days=1)),
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no debit transactions during that period."


def test_formatter_no_results_without_type_for_yesterday_uses_direct_fact() -> None:
    yesterday = lagos_today() - timedelta(days=1)
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            time_range=TimeRange(start=yesterday, end=yesterday),
            result_limit=1,
            result_reference="latest",
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "You had no transactions yesterday."


def test_formatter_search_shaped_no_results_for_yesterday_stays_generic() -> None:
    yesterday = lagos_today() - timedelta(days=1)
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(merchant=["mum"]),
            time_range=TimeRange(start=yesterday, end=yesterday),
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def test_formatter_no_results_without_type_uses_generic_message() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(intent=QueryIntent.TRANSACTION_LIST),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def _sample_list_result(query_snapshot: NormalizedQuery) -> QueryResult:
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
        query_snapshot=query_snapshot,
    )


def test_formatter_heading_uses_credit_context() -> None:
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Credit Transactions*"


def test_formatter_preserves_paginated_credit_list_shape_for_single_remaining_item() -> None:
    today = lagos_today()
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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
            time_range=TimeRange(start=today.replace(day=1), end=today),
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.TRANSACTION_LIST,
            context={"type": "transaction_list", "count": 1, "total_results": 6, "has_more": False},
        ),
    )

    response = QueryFormatter.format(result, current_page=1, locale="en")

    assert response.splitlines()[0] == "*Credit Transactions* — This Month"
    assert "Your last credit transaction was:" not in response
    assert "₦35,000" in response
    assert "_Showing 6-6 of 6_" in response


def test_formatter_heading_uses_category_spending_for_debit() -> None:
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit", category=["food"]),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Food Spending*"


def test_formatter_heading_includes_amount_scope_for_transaction_lists() -> None:
    today = lagos_today()
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit", min_amount=20000),
            time_range=TimeRange(start=today.replace(day=1), end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "*Debit Transactions* — Over ₦20,000 — This Month"


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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit", counterparty=["Mum"]),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
            answer_fact_field="date",
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(
            primary_text="You last paid Mum on March 24, 2026.",
            secondary_text="₦50,000 • Zenith Bank",
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "You last paid Mum on March 24, 2026.\n\n₦50,000 • Zenith Bank"
    assert "Transaction Details" not in response


def test_formatter_fact_no_results_prefers_natural_copy_under_direct_answer() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit", counterparty=["Mum"]),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
            answer_fact_field="date",
        ),
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
    )

    response = QueryFormatter.format(result, locale="en")

    assert response == "I couldn't find any payment to Mum in that period."


def test_formatter_heading_appends_account_and_today_suffix() -> None:
    today = lagos_today()
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(account_filter="Zenith"),
            time_range=TimeRange(start=today, end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Transactions* — Zenith — Today"


def test_formatter_heading_single_day_past_range_is_not_labeled_today() -> None:
    today = date.today()
    past_day = today - timedelta(days=1)
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=past_day, end=past_day),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    heading = response.splitlines()[0]
    past_label = past_day.strftime("%b %d").replace(" 0", " ")
    assert heading == f"*Transactions* — {past_label}–{past_label}"
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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit", merchant=["netflix"]),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21)),
            result_limit=1,
            result_reference="latest",
            answer_fact_field="date",
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context={"type": "single_transaction"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")
    lines = response.splitlines()

    assert lines[0] == "You last paid Netflix on March 21, 2026."
    assert "Your last debit transaction was:" in response


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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="credit"),
            time_range=TimeRange(start=date(2026, 3, 15), end=date(2026, 3, 21)),
            answer_fact_field="counterparty",
        ),
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context={"type": "single_transaction"},
        ),
    )

    response = QueryFormatter.format(result, locale="en")

    assert response.splitlines()[0] == "You received ₦35,000 from Johnson Mary on March 21, 2026."


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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit"),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 21)),
            result_reference="latest",
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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=Aggregation(type="breakdown", group_by="account"),
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


def test_formatter_breakdown_heading_includes_amount_scope_and_period() -> None:
    today = lagos_today()
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
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            filters=Filters(transaction_type="debit", min_amount=20000),
            aggregation=Aggregation(type="breakdown", group_by="account"),
            time_range=TimeRange(start=today.replace(day=1), end=today),
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

    assert response.splitlines()[0] == "Spending by account — Over ₦20,000 — This Month"
