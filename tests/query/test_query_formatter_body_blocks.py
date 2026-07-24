from datetime import date

from banking.transactions.query.models.domain import (
    Filters,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.presentation.formatter import QueryFormatter
from banking.transactions.query.utils.timezone import lagos_today
from shared.messaging.body_blocks import render_body_blocks_text
from tests.query.factories import make_query_request


def _contract(
    *,
    intent: QueryIntent = QueryIntent.TRANSACTION_LIST,
    filters: Filters | None = None,
    time_start: date = date(2026, 6, 11),
    time_end: date = date(2026, 6, 11),
) -> QueryRequest:
    return (
        make_query_request(
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
                    "source_account_number": "8067892221",
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
        query_request=_contract(filters=Filters(transaction_type="debit")),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert "I found 2 debit transactions for Jun 11." in rendered
    assert "*Jun 11*" in rendered
    assert rendered.count("*Jun 11*") == 1
    assert "• ₦30,000 — Sent to Mum · Wema · ···2221" in rendered
    assert "• ₦2,000 — Airtime for 08162511023 · Access Bank" in rendered
    assert "Showing 1-2 of 2" not in rendered


def test_show_evidence_blocks_replace_free_form_prefix_with_localized_lead() -> None:
    contract = _contract(filters=Filters(transaction_type="debit")).model_copy(
        update={"continuation_type": "show_evidence"}
    )
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        conversational_prefix="Show the underlying rows.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Tolu",
                amount=6000,
                date=date(2026, 7, 16),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "counterparty": "Tolu Adebayo",
                    "source_bank_name": "GTBank",
                    "source_account_number": "2010000002",
                    "recipient_account_number": "2010000001",
                },
            )
        ],
        query_request=contract,
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert "Here's the transaction behind that total." in rendered
    assert "Show the underlying rows." not in rendered
    assert "GTBank · ···0002" in rendered
    assert "···0001" not in rendered


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
        query_request=_contract(filters=Filters(transaction_type="debit")),
    )

    rendered = render_body_blocks_text(QueryFormatter.format_blocks(result, locale="en"))

    assert "*Jun 11*" in rendered
    assert "Failed · ₦50,000" in rendered
    assert "Transfer to Tolu Adebayo" in rendered
    assert "Sent to Tolu Adebayo" not in rendered


def test_empty_structural_summary_blocks_never_leak_internal_metadata() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="accounts:4|showing:1-0|total:0",
        items=[],
        query_request=_contract(
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
