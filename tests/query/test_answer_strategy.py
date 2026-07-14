from datetime import date

from banking.transactions.query.models.domain import (
    Filters,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.services.answers.strategy import select_answer_strategy


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
    assert selected.answer_context.primary_text == "You paid Mum on March 24, 2026."
    assert selected.followup_referent is not None
    assert selected.followup_referent.recipient_account == "8162511023"


def test_select_answer_strategy_does_not_direct_answer_structural_empty_list_summary() -> None:
    result = QueryResult(
        summary_text="accounts:4|showing:1-0|total:0",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 28), end=date(2026, 3, 28)),
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is None
    assert selected.answer_strategy == QueryAnswerStrategy.TRANSACTION_LIST


def test_select_answer_strategy_direct_answers_analytics_sum_without_list_dump() -> None:
    result = QueryResult(
        summary_text="You spent ₦42,000 today, across 2 transactions.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=32000,
                date=date(2026, 3, 28),
                metadata={"type": "debit"},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="debit"),
                aggregation={"type": "sum"},
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "You spent ₦42,000 today, across 2 transactions."


def test_single_beneficiary_summary_sets_followup_referent() -> None:
    result = QueryResult(
        summary_text="You sent Cowrywise the most this month.",
        items=[
            QueryResultItem(
                id="beneficiary-cowrywise",
                description="Cowrywise",
                amount=150000,
                date=date(2026, 3, 28),
                metadata={"recipient_name": "Cowrywise", "count": 3},
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.BENEFICIARY_SUMMARY,
                filters=Filters(transaction_type="debit"),
                aggregation={"type": "sum", "sort_by": "amount", "limit": 1},
                result_limit=1,
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
    assert selected.followup_referent is not None
    assert selected.followup_referent.label == "Cowrywise"
    assert selected.followup_referent.recipient_name == "Cowrywise"
    assert selected.followup_referent.entity_id == "beneficiary-cowrywise"


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


def test_select_answer_strategy_uses_latest_fact_without_ambiguity_for_multiple_matches() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(
                id="tx-latest",
                description="Payment to Tolu Adebayo",
                amount=5000,
                date=date(2026, 6, 29),
                metadata={"type": "debit", "recipient_name": "Tolu Adebayo", "recipient_bank_name": "Access Bank"},
            ),
            QueryResultItem(
                id="tx-older",
                description="Payment to Tolu Adebayo",
                amount=25000,
                date=date(2026, 6, 23),
                metadata={"type": "debit", "recipient_name": "Tolu Adebayo", "recipient_bank_name": "First Bank"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Tolu Adebayo"]),
                answer_fact_field="amount",
                result_reference="latest",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "The last transfer to Tolu Adebayo was ₦5,000."
    assert selected.answer_context.secondary_text == "Jun 29 • Access Bank"


def test_select_answer_strategy_uses_posted_status_for_history_item_without_status() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx-acme",
                description="Salary from Acme Corp",
                amount=950000,
                date=date(2026, 5, 13),
                metadata={
                    "type": "credit",
                    "transaction_type": "credit",
                    "counterparty": "Acme Corp",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 16)),
                answer_fact_field="status",
                result_reference="latest",
                result_limit=1,
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "That transaction is posted."
    assert selected.answer_context.secondary_text == "₦950,000 • May 13 • Acme Corp"


def test_select_answer_strategy_localizes_posted_status_for_pidgin() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx-acme",
                description="Salary from Acme Corp",
                amount=950000,
                date=date(2026, 5, 13),
                metadata={
                    "type": "credit",
                    "transaction_type": "credit",
                    "counterparty": "Acme Corp",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 16)),
                answer_fact_field="status",
                result_reference="latest",
                result_limit=1,
            )
        ),
    )

    selected = select_answer_strategy(result, locale="pcm")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "That transaction don post."
    assert selected.answer_context.secondary_text == "₦950,000 • May 13 • Acme Corp"


def test_select_answer_strategy_explains_failed_bank_posted_mismatch() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Tolu",
                amount=6000,
                date=date(2026, 5, 16),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "recipient_name": "Tolu",
                    "display_status": "failed",
                    "local_status": "failed",
                    "bank_status": "posted",
                    "needs_review": True,
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                answer_fact_field="status",
                result_reference="latest",
                result_limit=1,
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is not None
    assert (
        selected.answer_context.primary_text
        == "Our app record says that transaction failed, but a matching debit is posted in your bank history. Please ask support to review it."
    )


def test_select_answer_strategy_explains_processing_bank_posted_status() -> None:
    result = QueryResult(
        summary_text="accounts:1|showing:1-1|total:1",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Tolu",
                amount=6000,
                date=date(2026, 5, 16),
                metadata={
                    "type": "debit",
                    "transaction_type": "transfer",
                    "recipient_name": "Tolu",
                    "display_status": "processing",
                    "local_status": "processing",
                    "bank_status": "posted",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                answer_fact_field="status",
                result_reference="latest",
                result_limit=1,
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is not None
    assert (
        selected.answer_context.primary_text
        == "That transaction is still processing in our app, but a matching debit is posted in your bank history."
    )


def test_select_answer_strategy_uses_status_aliases() -> None:
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
                    "provider_status": "processing",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="status",
                result_reference="latest",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "That transaction is processing."


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


def test_select_answer_strategy_answers_reference_fact_directly() -> None:
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
                    "recipient_name": "Mum",
                    "recipient_bank_name": "Opay",
                    "transaction_id": "ref_123",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="reference",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "The reference number for the Mum transaction is ref_123."
    assert selected.answer_context.secondary_text == "₦50,000 • Mar 24 • Opay"


def test_select_answer_strategy_prefers_provider_reference_for_reference_fact() -> None:
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
                    "recipient_name": "Mum",
                    "provider_reference": "mono_ref_123",
                    "bank_transaction_id": "bank_ref_456",
                    "transaction_id": "internal_ref_789",
                },
            )
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_SEARCH,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                answer_fact_field="reference",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "The reference number for the Mum transaction is mono_ref_123."


def test_select_answer_strategy_answers_existence_yes_with_total() -> None:
    result = QueryResult(
        summary_text="You spent *₦70,000* in that period, across 2 transactions.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 3, 24),
                metadata={"type": "debit", "recipient_name": "Mum"},
            ),
            QueryResultItem(
                id="tx2",
                description="Transfer to Mum",
                amount=20000,
                date=date(2026, 3, 20),
                metadata={"type": "debit", "recipient_name": "Mum"},
            ),
        ],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                request_shape="existence",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "Yes. I found 2 payments to Mum in that period, totaling ₦70,000."


def test_select_answer_strategy_answers_existence_no_without_coverage_disclaimer() -> None:
    result = QueryResult(
        summary_text="You didn't spend anything in that period.",
        items=[],
        query_contract=_query_contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                filters=Filters(transaction_type="debit", counterparty=["Mum"]),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 27)),
                request_shape="existence",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "No. I don't see any payment to Mum in that period."
    assert "local" not in selected.answer_context.primary_text.lower()


def test_select_answer_strategy_uses_compact_followup_fact_answer_without_evidence() -> None:
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
                continuation_type="drill_down",
            )
        ),
    )

    selected = select_answer_strategy(result, locale="en")

    assert selected.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER
    assert selected.answer_context is not None
    assert selected.answer_context.primary_text == "You paid Mum on March 24, 2026."
    assert selected.answer_context.secondary_text is None
