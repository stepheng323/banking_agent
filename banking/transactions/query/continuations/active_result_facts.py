"""Fact-answer helpers for active query result continuations."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.services.answers.fact_answer import build_direct_fact_answer
from banking.transactions.query.services.fetching.fetch import apply_filters, apply_time_window, parse_date


def _coerce_cached_transactions(session: dict[str, Any]) -> list[dict[str, Any]]:
    raw_transactions = session.get("cached_transactions")
    if not isinstance(raw_transactions, list):
        return []
    return [item for item in raw_transactions if isinstance(item, dict)]


def _transaction_sort_key(transaction: dict[str, Any]) -> tuple[str, str]:
    return (str(transaction.get("date", "")), str(transaction.get("id", "")))


def _query_result_item_from_transaction(
    transaction: dict[str, Any],
    *,
    fallback_id: str,
    language: str,
) -> QueryResultItem:
    return QueryResultItem(
        id=str(transaction.get("id") or fallback_id)[:8],
        description=str(transaction.get("narration") or render_message("query.format.narration.transaction", language)),
        amount=abs(float(transaction.get("amount") or 0.0)),
        date=parse_date(str(transaction.get("date") or "")),
        metadata={
            "type": transaction.get("type"),
            "bank_name": transaction.get("bank_name", ""),
            "transaction_type": transaction.get("transaction_type"),
            "status": transaction.get("status", ""),
            "transaction_id": transaction.get("transaction_id") or transaction.get("id"),
            "counterparty": transaction.get("counterparty"),
            "counterparty_role": transaction.get("counterparty_role"),
            "recipient_name": transaction.get("recipient_name") or transaction.get("counterparty"),
            "recipient_account": transaction.get("recipient_account"),
            "recipient_account_number": transaction.get("recipient_account_number"),
            "recipient_bank_name": transaction.get("recipient_bank_name"),
            "recipient_bank_code": transaction.get("recipient_bank_code"),
            "source_account_id": transaction.get("source_account_id"),
            "source_account_label": transaction.get("source_account_label"),
        },
    )


def maybe_build_fact_answer_from_decision(
    *,
    decision: Any,
    session: dict[str, Any],
    restored_query_result: QueryResult | None,
    session_query_contract: QueryExecutionContract | None,
    language: str,
) -> dict[str, Any] | None:
    if getattr(decision, "decision", None) != "continuation":
        return None
    if getattr(decision, "continuation_type", None) != "drill_down":
        return None
    if getattr(decision, "drill_down_action", None) != "answer_fact":
        return None
    if session_query_contract is None:
        return None

    raw_fact_field = getattr(decision, "fact_field", None) or session_query_contract.answer_fact_field
    if raw_fact_field == "recipient":
        raw_fact_field = "counterparty"
    if raw_fact_field not in {
        "date",
        "counterparty",
        "amount",
        "bank",
        "status",
        "description",
        "reference",
        "account",
        "direction",
        "category",
    }:
        return None

    selected_index_raw = session.get("selected_item_index")
    selected_index = (
        int(selected_index_raw) if isinstance(selected_index_raw, int) and selected_index_raw >= 0 else None
    )
    raw_drill_index = getattr(decision, "drill_down_index", None)
    drill_index = raw_drill_index if isinstance(raw_drill_index, int) and raw_drill_index >= 0 else selected_index

    if drill_index is None:
        restored_items = restored_query_result.items if restored_query_result is not None else None
        if not restored_items or len(restored_items) != 1:
            return None
        drill_index = 0

    item: QueryResultItem | None = None
    if restored_query_result is not None and restored_query_result.items:
        bounded_index = max(0, min(drill_index, len(restored_query_result.items) - 1))
        if bounded_index == drill_index:
            item = restored_query_result.items[bounded_index]

    if item is None:
        cached_transactions = _coerce_cached_transactions(session)
        if cached_transactions:
            current_window_start = (
                session_query_contract.time_range.start.isoformat() if session_query_contract.time_range else None
            )
            current_window_end = (
                session_query_contract.time_range.end.isoformat() if session_query_contract.time_range else None
            )
            scoped_transactions = apply_time_window(
                cached_transactions,
                window_start=current_window_start,
                window_end=current_window_end,
            )
            filtered_transactions = (
                apply_filters(scoped_transactions, session_query_contract.filters)
                if session_query_contract.filters
                else list(scoped_transactions)
            )
            reverse_sort = session_query_contract.result_reference != "oldest"
            ranked_transactions = sorted(filtered_transactions, key=_transaction_sort_key, reverse=reverse_sort)
            if drill_index is not None and 0 <= drill_index < len(ranked_transactions):
                item = _query_result_item_from_transaction(
                    ranked_transactions[drill_index],
                    fallback_id=f"tx-{drill_index}",
                    language=language,
                )

    if item is None:
        return None

    answer_contract = (
        session_query_contract.model_copy(update={"result_reference": None})
        if selected_index is None or drill_index != selected_index
        else session_query_contract
    )
    answer_context = build_direct_fact_answer(
        item,
        query_contract=answer_contract,
        fact_field=raw_fact_field,
        locale=language,
    )
    lines = [answer_context.primary_text]
    if answer_context.secondary_text:
        lines.extend(["", answer_context.secondary_text])
    return {
        "transaction_outcome": TransactionOutcome.OK,
        "response": "\n".join(lines),
        "session_active": True,
        "flow_state": "complete",
        "resolver_message": None,
        "show_expanded": bool(session.get("show_expanded", False)),
        "current_page": session.get("current_page", 0),
        "selected_item_index": drill_index,
        "_query_session_transition": "answer_fact_active_result",
    }


__all__ = ["maybe_build_fact_answer_from_decision"]
