"""Select explicit answer strategies for query results."""

from __future__ import annotations

from apps.core.src.agent.graphs.query.models import (
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from apps.core.src.agent.graphs.query.services.continuity import build_soft_clarification
from apps.core.src.agent.graphs.query.services.contracts import build_focus_referent
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today


def select_answer_strategy(result: QueryResult, *, locale: str = "en") -> QueryResult:
    """Attach an explicit answer strategy to an execution result."""
    if result.answer_strategy is not None:
        return result

    query_contract = result.query_contract

    if query_contract and query_contract.answer_fact_field in {"date", "counterparty", "amount", "bank"}:
        return _apply_fact_answer_strategy(result, query_contract=query_contract, locale=locale)

    if query_contract and query_contract.intent in {
        QueryIntent.ANALYTICS_SUMMARY,
        QueryIntent.BENEFICIARY_SUMMARY,
        QueryIntent.TIME_COMPARISON,
        QueryIntent.AFFORDABILITY,
    }:
        result.answer_strategy = QueryAnswerStrategy.SUMMARY_LIST
        return result

    if result.summary_text and not result.items:
        result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
        result.answer_context = QueryAnswerContext(primary_text=result.summary_text)
        return result

    result.answer_strategy = QueryAnswerStrategy.TRANSACTION_LIST
    return result


def build_direct_fact_answer(
    item: QueryResultItem,
    *,
    query_contract: QueryExecutionContract | None,
    fact_field: str,
    locale: str = "en",
) -> QueryAnswerContext:
    """Build a compact natural answer for a single fact-style transaction answer."""
    if locale != "en":
        return _build_non_english_fact_answer(item, fact_field=fact_field)

    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    tx_type = str(metadata.get("type") or "").strip().lower()
    counterparty = _counterparty_label(item, query_contract=query_contract)
    bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip() or None
    amount = f"₦{abs(float(item.amount)):,.0f}"
    date_text = item.date.strftime("%B %d, %Y")

    if fact_field == "date":
        if counterparty and tx_type == "debit":
            primary = f"You last paid {counterparty} on {date_text}."
        elif counterparty and tx_type == "credit":
            primary = f"The last credit from {counterparty} was on {date_text}."
        else:
            primary = f"That transaction was on {date_text}."
    elif fact_field == "counterparty":
        if counterparty and tx_type == "credit":
            primary = f"You received {amount} from {counterparty} on {date_text}."
        elif counterparty and tx_type == "debit":
            primary = f"You paid {counterparty} {amount} on {date_text}."
        elif counterparty:
            primary = f"That transaction was with {counterparty}."
        else:
            primary = "I found the transaction, but I couldn't resolve the counterparty name cleanly."
    elif fact_field == "amount":
        if counterparty and tx_type == "debit":
            primary = f"You paid {counterparty} {amount}."
        elif counterparty and tx_type == "credit":
            primary = f"You received {amount} from {counterparty}."
        else:
            primary = f"That transaction was {amount}."
    elif fact_field == "bank":
        if bank_name and counterparty and tx_type == "debit":
            primary = f"That payment to {counterparty} went through {bank_name}."
        elif bank_name and counterparty and tx_type == "credit":
            primary = f"That credit from {counterparty} came through {bank_name}."
        elif bank_name:
            primary = f"That transaction went through {bank_name}."
        else:
            primary = "I found the transaction, but the bank name is not available."
    else:
        primary = item.description

    secondary = _build_evidence_line(item, query_contract=query_contract, skip_field=fact_field)
    return QueryAnswerContext(primary_text=primary, secondary_text=secondary)


def _apply_fact_answer_strategy(result: QueryResult, *, query_contract: QueryExecutionContract, locale: str) -> QueryResult:
    fact_field = query_contract.answer_fact_field or "date"

    if not result.items:
        result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
        return result

    if len(result.items) > 1:
        result.answer_strategy = QueryAnswerStrategy.CLARIFY
        result.answer_context = QueryAnswerContext(
            primary_text=build_soft_clarification(result.items, context="Which transaction", locale=locale)
        )
        return result

    item = result.items[0]
    result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
    result.answer_context = build_direct_fact_answer(
        item,
        query_contract=query_contract,
        fact_field=fact_field,
        locale=locale,
    )
    focus_referent = build_focus_referent(item, query_contract=query_contract)
    if focus_referent is not None:
        result.followup_referent = focus_referent
    return result


def _counterparty_label(item: QueryResultItem, *, query_contract: QueryExecutionContract | None) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    for candidate in (
        metadata.get("recipient_name"),
        metadata.get("counterparty"),
        _first_filter_value(query_contract.filters.counterparty if query_contract and query_contract.filters else None),
    ):
        if isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned:
                return cleaned
    return None


def _first_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _build_evidence_line(
    item: QueryResultItem,
    *,
    query_contract: QueryExecutionContract | None,
    skip_field: str,
) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
    counterparty = _counterparty_label(item, query_contract=query_contract)
    date_text = item.date.strftime("%b %d")
    amount = f"₦{abs(float(item.amount)):,.0f}"

    parts: list[str] = []
    if skip_field != "amount":
        parts.append(amount)
    if skip_field != "date":
        parts.append(date_text)
    if skip_field != "counterparty" and counterparty:
        parts.append(counterparty)
    if skip_field != "bank" and bank_name:
        parts.append(bank_name)

    return " • ".join(parts[:3]) or None


def _build_non_english_fact_answer(item: QueryResultItem, *, fact_field: str) -> QueryAnswerContext:
    date_text = item.date.strftime("%B %d, %Y")
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    if fact_field == "date":
        return QueryAnswerContext(primary_text=f"*Date:* {date_text}")
    if fact_field == "amount":
        return QueryAnswerContext(primary_text=f"*Amount:* ₦{abs(float(item.amount)):,.2f}")
    if fact_field == "bank":
        bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
        return QueryAnswerContext(primary_text=f"*Bank:* {bank_name}" if bank_name else "*Bank:* Unknown")
    counterparty = str(metadata.get("counterparty") or metadata.get("recipient_name") or "").strip()
    return QueryAnswerContext(primary_text=f"*Counterparty:* {counterparty}" if counterparty else item.description)


def build_fact_no_results_text(query_contract: QueryExecutionContract | None) -> str | None:
    """Return compact natural no-results copy for fact queries when available."""
    if query_contract is None or query_contract.filters is None:
        return None

    tx_type = query_contract.filters.transaction_type
    counterparty = _first_filter_value(query_contract.filters.counterparty)
    time_range = query_contract.time_range
    today = lagos_today()

    if time_range is None:
        suffix = "in that period"
    elif time_range.start == time_range.end == today:
        suffix = "today"
    else:
        suffix = "in that period"

    if tx_type == "credit" and counterparty:
        return f"I couldn't find any credit from {counterparty} {suffix}."
    if tx_type == "debit" and counterparty:
        return f"I couldn't find any payment to {counterparty} {suffix}."
    if tx_type == "credit":
        return f"I couldn't find any matching credit {suffix}."
    if tx_type == "debit":
        return f"I couldn't find any matching debit {suffix}."
    return None
