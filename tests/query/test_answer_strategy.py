from datetime import date

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryAnswerStrategy,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.answer_strategy import select_answer_strategy


def test_select_answer_strategy_uses_direct_answer_for_single_fact_match() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "recipient_name": "Mum",
                    "recipient_account_number": "8162511023",
                    "recipient_bank_name": "Opay",
                    "recipient_bank_code": "999992",
                },
            )
        ],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit", counterparty=["Mum"]),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
            answer_fact_field="date",
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "You last paid Mum on March 24, 2026."
    assert selected.followup_referent is not None
    assert selected.followup_referent.recipient_account == "8162511023"


def test_select_answer_strategy_uses_clarify_for_ambiguous_fact_match() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(id="tx1", description="Transfer to Mum", amount=50000, date=date(2026, 3, 24)),
            QueryResultItem(id="tx2", description="Transfer to Mum", amount=20000, date=date(2026, 3, 20)),
        ],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            filters=Filters(transaction_type="debit", counterparty=["Mum"]),
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
            answer_fact_field="date",
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.CLARIFY
    assert selected.answer_context is not None
    assert "I'm not sure which one you mean" in selected.answer_context.primary_text
