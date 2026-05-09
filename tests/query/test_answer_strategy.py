from datetime import date

from apps.chat.src.agent.graphs.query.models import (
    Filters,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.chat.src.agent.graphs.query.services.answer_strategy import select_answer_strategy


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 28)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


def _query_contract(query: QueryIR) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(query)


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
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="date",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "Looks like you paid Mum on March 24, 2026."
    assert selected.followup_referent is not None
    assert selected.followup_referent.recipient_account == "8162511023"


def test_select_answer_strategy_uses_localized_reply_for_single_fact_match() -> None:
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
                    "recipient_bank_name": "Opay",
                },
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
    )

    selected = select_answer_strategy(result, locale="yo")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "O san owo si Mum ni March 24, 2026."
    assert selected.answer_context.secondary_text == "₦50,000 • Opay"


def test_select_answer_strategy_uses_latest_counterparty_reply_with_compact_evidence() -> None:
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
                    "recipient_bank_name": "Opay",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="counterparty",
                result_reference="latest",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "The last person you sent money to was Mum."
    assert selected.answer_context.secondary_text == "₦50,000 • Mar 24 • Opay"


def test_select_answer_strategy_uses_latest_amount_reply() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"type": "debit", "recipient_name": "Mum", "recipient_bank_name": "Opay"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="amount",
                result_reference="latest",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "The last transfer to Mum was ₦50,000."
    assert selected.answer_context.secondary_text == "Mar 24 • Opay"


def test_select_answer_strategy_uses_latest_bank_reply() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"type": "debit", "recipient_name": "Mum", "recipient_bank_name": "Opay"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="bank",
                result_reference="latest",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "That last transfer to Mum went through Opay."
    assert selected.answer_context.secondary_text == "₦50,000 • Mar 24"


def test_select_answer_strategy_uses_clarify_for_ambiguous_fact_match() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(id="tx1", description="Transfer to Mum", amount=50000, date=date(2026, 3, 24)),
            QueryResultItem(id="tx2", description="Transfer to Mum", amount=20000, date=date(2026, 3, 20)),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="date",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.CLARIFY
    assert selected.answer_context is not None
    assert "I'm not sure which one you mean" in selected.answer_context.primary_text
