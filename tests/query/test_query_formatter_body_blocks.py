from datetime import date

from banking.transactions.query.models.domain import (
    Filters,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.presentation.formatter import QueryFormatter
from banking.transactions.query.utils.timezone import lagos_today
from shared.messaging.body_blocks import render_body_blocks_text


def _contract(
    *,
    intent: QueryIntent = QueryIntent.TRANSACTION_LIST,
    filters: Filters | None = None,
    time_start: date = date(2026, 6, 11),
    time_end: date = date(2026, 6, 11),
) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(
        QueryIR(
            intent=intent,
            filters=filters,
            time_range=TimeRange(start=time_start, end=time_end),
        )
    )


def test_direct_answer_blocks_strip_markdown_but_keep_answer_copy() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(
            primary_text="You spent *₦20,000* today, across 2 transactions.",
            secondary_text="Settled transactions only.",
        ),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert rendered == "You spent ₦20,000 today, across 2 transactions.\n\nSettled transactions only."


def test_transaction_list_blocks_use_mobile_spacing_instead_of_dense_rows() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(
                id="tx1",
                description="Money sent",
                amount=30000,
                date=date(2026, 6, 11),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "counterparty": "Mum",
                    "bank_name": "Wema",
                    "recipient_account": "8067892221",
                    "status": "successful",
                },
            ),
            QueryResultItem(
                id="tx2",
                description="Airtime purchase",
                amount=2000,
                date=date(2026, 6, 11),
                metadata={
                    "type": "debit",
                    "transaction_type": "airtime",
                    "counterparty": "08162511023",
                    "bank_name": "Access Bank",
                },
            ),
        ],
        query_contract=_contract(filters=Filters(transaction_type="debit")),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert rendered.startswith("Debit Transactions")
    assert "Jun 11" in rendered
    assert "₦30,000 • Sent to Mum\nWema • ···2221" in rendered
    assert "\n\n₦2,000 • Airtime for 08162511023\nAccess Bank" in rendered
    assert "Showing 1-2 of 2" in rendered


def test_failed_transaction_list_blocks_do_not_label_failed_transfer_as_sent() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Tolu",
                amount=50000,
                date=date(2026, 6, 11),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "counterparty": "Tolu Adebayo",
                    "bank_name": "GTBank",
                    "display_status": "failed",
                },
            )
        ],
        query_contract=_contract(filters=Filters(transaction_type="debit")),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert "Failed transfer — Tolu Adebayo" in rendered
    assert "Sent to Tolu Adebayo" not in rendered


def test_empty_structural_summary_blocks_never_leak_internal_metadata() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="accounts:4|showing:1-0|total:0",
        items=[],
        query_contract=_contract(
            filters=Filters(transaction_type="debit"),
            time_start=today,
            time_end=today,
        ),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert "You had no debit transactions today." in rendered
    assert "accounts:" not in rendered
    assert "showing:" not in rendered
    assert "total:" not in rendered
