"""Select explicit answer strategies for query results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

from apps.chat.src.agent.graphs.query.capabilities import QUERY_LIMITS
from apps.chat.src.agent.graphs.query.models import (
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryFactField,
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from apps.chat.src.agent.graphs.query.services.continuity import build_soft_clarification
from apps.chat.src.agent.graphs.query.services.contracts import build_focus_referent
from apps.chat.src.agent.graphs.query.utils.timezone import lagos_today
from shared.formatters.transaction_copy import format_transaction_evidence_line, format_transaction_status_reply
from shared.i18n import render_message

FactKind = QueryFactField
FactDirection = Literal["debit", "credit", "unknown"]


@dataclass(frozen=True)
class DirectAnswerFact:
    fact_kind: FactKind
    direction: FactDirection
    counterparty: str | None
    amount_text: str
    bank_name: str | None
    date_text: str
    fallback_description: str
    status_text: str | None
    local_status_text: str | None
    bank_status_text: str | None
    needs_review: bool
    reference_text: str | None
    account_text: str | None
    direction_text: str | None
    category_text: str | None
    result_reference: Literal["latest", "oldest"] | None


def select_answer_strategy(result: QueryResult, *, locale: str = "en") -> QueryResult:
    """Attach an explicit answer strategy to an execution result."""
    if result.answer_strategy is not None:
        return result

    query_contract = result.query_contract

    if query_contract and query_contract.request_shape == "existence":
        return _apply_existence_answer_strategy(result, query_contract=query_contract, locale=locale)

    if query_contract and query_contract.answer_fact_field in {
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
    """Build a compact conversational answer for a single fact-style transaction answer."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    direction = _normalize_direction(str(metadata.get("type") or ""))
    fact = DirectAnswerFact(
        fact_kind=_normalize_fact_kind(fact_field),
        direction=direction,
        counterparty=_counterparty_label(item, query_contract=query_contract),
        bank_name=str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip() or None,
        amount_text=f"₦{abs(float(item.amount)):,.0f}",
        date_text=item.date.strftime("%B %d, %Y"),
        fallback_description=item.description,
        status_text=_status_label(metadata) or _implicit_history_status_label(item, metadata),
        local_status_text=_status_value(metadata.get("local_status")),
        bank_status_text=_status_value(metadata.get("bank_status")),
        needs_review=bool(metadata.get("needs_review")),
        reference_text=_reference_label(item),
        account_text=_account_label(metadata),
        direction_text=_direction_label(metadata),
        category_text=_category_label(metadata),
        result_reference=query_contract.result_reference if query_contract is not None else None,
    )
    primary, used_fields = _compose_direct_reply(fact, locale=locale)
    secondary = _build_evidence_line(item, query_contract=query_contract, used_fields=used_fields, locale=locale)
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


def _apply_existence_answer_strategy(
    result: QueryResult,
    *,
    query_contract: QueryExecutionContract,
    locale: str,
) -> QueryResult:
    result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
    result.answer_context = QueryAnswerContext(
        primary_text=build_existence_answer(result, query_contract=query_contract, locale=locale)
    )
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


def _normalize_fact_kind(fact_field: str) -> FactKind:
    if fact_field in {
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
        return cast(FactKind, fact_field)
    return "date"


def _normalize_direction(raw_value: str) -> FactDirection:
    normalized = raw_value.strip().lower()
    if normalized == "debit":
        return "debit"
    if normalized == "credit":
        return "credit"
    return "unknown"


def _compose_direct_reply(fact: DirectAnswerFact, *, locale: str) -> tuple[str, set[str]]:
    if fact.result_reference == "latest":
        latest_reply = _compose_latest_direct_reply(fact, locale=locale)
        if latest_reply is not None:
            return latest_reply

    if fact.fact_kind == "date":
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.date.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "date": fact.date_text},
                ),
                {"date", "counterparty"},
            )
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.date.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "date": fact.date_text},
                ),
                {"date", "counterparty"},
            )
        return (
            render_message("query.reply.date.generic", locale, {"date": fact.date_text}),
            {"date"},
        )

    if fact.fact_kind == "counterparty":
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.counterparty.credit_named",
                    locale,
                    {
                        "amount": fact.amount_text,
                        "counterparty": fact.counterparty,
                        "date": fact.date_text,
                    },
                ),
                {"amount", "counterparty", "date"},
            )
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.counterparty.debit_named",
                    locale,
                    {
                        "amount": fact.amount_text,
                        "counterparty": fact.counterparty,
                        "date": fact.date_text,
                    },
                ),
                {"amount", "counterparty", "date"},
            )
        if fact.counterparty:
            return (
                render_message(
                    "query.reply.counterparty.generic",
                    locale,
                    {"counterparty": fact.counterparty},
                ),
                {"counterparty"},
            )
        return (render_message("query.reply.counterparty.unavailable", locale), set())

    if fact.fact_kind == "amount":
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.amount.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "amount": fact.amount_text},
                ),
                {"counterparty", "amount"},
            )
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.amount.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "amount": fact.amount_text},
                ),
                {"counterparty", "amount"},
            )
        return (
            render_message("query.reply.amount.generic", locale, {"amount": fact.amount_text}),
            {"amount"},
        )

    if fact.fact_kind == "bank":
        if fact.bank_name and fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.bank.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "bank_name": fact.bank_name},
                ),
                {"counterparty", "bank"},
            )
        if fact.bank_name and fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.bank.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "bank_name": fact.bank_name},
                ),
                {"counterparty", "bank"},
            )
        if fact.bank_name:
            return (
                render_message("query.reply.bank.generic", locale, {"bank_name": fact.bank_name}),
                {"bank"},
            )
        return (render_message("query.reply.bank.unavailable", locale), set())

    if fact.fact_kind == "status":
        return (
            format_transaction_status_reply(
                fact.status_text,
                local_status=fact.local_status_text,
                bank_status=fact.bank_status_text,
                needs_review=fact.needs_review,
                locale=locale,
            ),
            {"status"} if fact.status_text else set(),
        )
    if fact.fact_kind == "description":
        return (f"It was for {fact.fallback_description}.", {"description"})
    if fact.fact_kind == "reference":
        return (
            f"The reference is {fact.reference_text}."
            if fact.reference_text
            else "I found the transaction, but I couldn't confirm the reference.",
            {"reference"} if fact.reference_text else set(),
        )
    if fact.fact_kind == "account":
        return (
            f"It was on {fact.account_text}."
            if fact.account_text
            else "I found the transaction, but I couldn't confirm the account.",
            {"account"} if fact.account_text else set(),
        )
    if fact.fact_kind == "direction":
        return (
            f"It was {fact.direction_text}."
            if fact.direction_text
            else "I found the transaction, but I couldn't confirm the direction.",
            {"direction"} if fact.direction_text else set(),
        )
    if fact.fact_kind == "category":
        return (
            f"It was categorized as {fact.category_text}."
            if fact.category_text
            else "I found the transaction, but I couldn't confirm the category.",
            {"category"} if fact.category_text else set(),
        )
    return (render_message("query.reply.bank.unavailable", locale), set())


def _compose_latest_direct_reply(fact: DirectAnswerFact, *, locale: str) -> tuple[str, set[str]] | None:
    if fact.fact_kind == "date":
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.latest.date.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "date": fact.date_text},
                ),
                {"date", "counterparty"},
            )
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.latest.date.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "date": fact.date_text},
                ),
                {"date", "counterparty"},
            )
        return (
            render_message("query.reply.latest.date.generic", locale, {"date": fact.date_text}),
            {"date"},
        )

    if fact.fact_kind == "counterparty":
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.latest.counterparty.credit_named",
                    locale,
                    {"counterparty": fact.counterparty},
                ),
                {"counterparty"},
            )
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.latest.counterparty.debit_named",
                    locale,
                    {"counterparty": fact.counterparty},
                ),
                {"counterparty"},
            )
        if fact.counterparty:
            return (
                render_message(
                    "query.reply.latest.counterparty.generic",
                    locale,
                    {"counterparty": fact.counterparty},
                ),
                {"counterparty"},
            )
        return (render_message("query.reply.counterparty.unavailable", locale), set())

    if fact.fact_kind == "amount":
        if fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.latest.amount.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "amount": fact.amount_text},
                ),
                {"counterparty", "amount"},
            )
        if fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.latest.amount.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "amount": fact.amount_text},
                ),
                {"counterparty", "amount"},
            )
        return (
            render_message("query.reply.latest.amount.generic", locale, {"amount": fact.amount_text}),
            {"amount"},
        )

    if fact.fact_kind == "bank":
        if fact.bank_name and fact.counterparty and fact.direction == "debit":
            return (
                render_message(
                    "query.reply.latest.bank.debit_named",
                    locale,
                    {"counterparty": fact.counterparty, "bank_name": fact.bank_name},
                ),
                {"counterparty", "bank"},
            )
        if fact.bank_name and fact.counterparty and fact.direction == "credit":
            return (
                render_message(
                    "query.reply.latest.bank.credit_named",
                    locale,
                    {"counterparty": fact.counterparty, "bank_name": fact.bank_name},
                ),
                {"counterparty", "bank"},
            )
        if fact.bank_name:
            return (
                render_message("query.reply.latest.bank.generic", locale, {"bank_name": fact.bank_name}),
                {"bank"},
            )
    return None


def _build_evidence_line(
    item: QueryResultItem,
    *,
    query_contract: QueryExecutionContract | None,
    used_fields: set[str],
    locale: str,
) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
    counterparty = _counterparty_label(item, query_contract=query_contract)
    return format_transaction_evidence_line(
        amount=item.amount,
        date_value=item.date,
        counterparty=counterparty,
        bank_name=bank_name or None,
        used_fields=used_fields,
        locale=locale,
    )


def build_existence_answer(
    result: QueryResult,
    *,
    query_contract: QueryExecutionContract,
    locale: str = "en",
) -> str:
    """Build direct yes/no copy for transaction existence questions."""
    del locale
    items = result.items or []
    count = len(items)
    filters = query_contract.filters
    tx_type = filters.transaction_type if filters else None
    counterparty = _first_filter_value(filters.counterparty if filters else None)
    merchant = _first_filter_value(filters.merchant if filters else None)
    category = _first_filter_value(filters.category if filters else None)
    target = counterparty or merchant or category
    target_phrase = _target_phrase(target, category=category)
    time_phrase = _time_phrase(query_contract)

    if count == 0:
        return _build_existence_no_match(tx_type=tx_type, target_phrase=target_phrase, time_phrase=time_phrase)

    total = sum(abs(float(item.amount)) for item in items)
    amount = f"₦{total:,.0f}"
    if tx_type == "credit":
        if target_phrase:
            if count == 1:
                return f"Yes. You received {amount} from {target_phrase}{time_phrase}."
            return f"Yes. I found {count} credits from {target_phrase}{time_phrase}, totaling {amount}."
        if count == 1:
            return f"Yes. You received {amount}{time_phrase}."
        return f"Yes. I found {count} credits{time_phrase}, totaling {amount}."
    if tx_type == "debit":
        if target_phrase:
            if category:
                if count == 1:
                    return f"Yes. You spent {amount} on {target_phrase}{time_phrase}."
                return f"Yes. I found {count} payments for {target_phrase}{time_phrase}, totaling {amount}."
            if count == 1:
                return f"Yes. You sent {amount} to {target_phrase}{time_phrase}."
            return f"Yes. I found {count} payments to {target_phrase}{time_phrase}, totaling {amount}."
        if count == 1:
            return f"Yes. You spent {amount}{time_phrase}."
        return f"Yes. I found {count} debits{time_phrase}, totaling {amount}."
    if target_phrase:
        if count == 1:
            return f"Yes. I found one transaction with {target_phrase}{time_phrase} for {amount}."
        return f"Yes. I found {count} transactions with {target_phrase}{time_phrase}, totaling {amount}."
    if count == 1:
        return f"Yes. I found one transaction{time_phrase} for {amount}."
    return f"Yes. I found {count} transactions{time_phrase}, totaling {amount}."


def _build_existence_no_match(*, tx_type: str | None, target_phrase: str | None, time_phrase: str) -> str:
    if tx_type == "credit":
        if target_phrase:
            return f"No. I don't see a matching credit from {target_phrase}{time_phrase}."
        return f"No. I don't see a matching credit{time_phrase}."
    if tx_type == "debit":
        if target_phrase:
            return f"No. I don't see any payment to {target_phrase}{time_phrase}."
        return f"No. I don't see a matching debit{time_phrase}."
    if target_phrase:
        return f"No. I don't see a matching transaction with {target_phrase}{time_phrase}."
    return f"No. I don't see a matching transaction{time_phrase}."


def _target_phrase(target: str | None, *, category: str | None) -> str | None:
    if not target:
        return None
    cleaned = target.strip()
    if not cleaned:
        return None
    if category:
        return cleaned.replace("_", " ")
    return cleaned


def _time_phrase(query_contract: QueryExecutionContract) -> str:
    time_range = query_contract.time_range
    if time_range is None:
        return ""
    today = lagos_today()
    if time_range.start == time_range.end == today:
        return " today"
    yesterday = today.fromordinal(today.toordinal() - 1)
    if time_range.start == time_range.end == yesterday:
        return " yesterday"
    if time_range.start.day == 1 and time_range.end == today and time_range.start.year == today.year and time_range.start.month == today.month:
        return " this month"
    if time_range.start == today.fromordinal(today.toordinal() - today.weekday()) and time_range.end == today:
        return " this week"
    return " in that period"


def _status_label(metadata: dict[str, object]) -> str | None:
    for key in ("display_status", "status", "provider_status", "transaction_status", "tx_status", "final_status"):
        status = _status_value(metadata.get(key))
        if status:
            return status
    return None


def _status_value(value: object) -> str | None:
    status = str(value or "").strip()
    if not status:
        return None
    lowered = status.lower()
    if lowered in {"success", "successful", "completed"}:
        return "successful"
    return status.replace("_", " ").lower()


def _implicit_history_status_label(item: QueryResultItem, metadata: dict[str, object]) -> str | None:
    """Bank-history items without provider status are already posted entries."""
    if item.description and item.date and item.amount is not None:
        return "posted"
    if metadata.get("posted_at") or metadata.get("posted_date"):
        return "posted"
    return None


def _reference_label(item: QueryResultItem) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    for key in ("transaction_id", "reference", "ref"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    item_id = str(item.id or "").strip()
    return item_id or None


def _account_label(metadata: dict[str, object]) -> str | None:
    for key in ("source_account_label", "source_account_number", "account_number", "bank_name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return None


def _direction_label(metadata: dict[str, object]) -> str | None:
    direction = str(metadata.get("direction") or metadata.get("transaction_type") or metadata.get("type") or "").strip()
    lowered = direction.lower()
    if lowered == "debit":
        return "an outgoing debit"
    if lowered == "credit":
        return "an incoming credit"
    return lowered.replace("_", " ") if lowered else None


def _category_label(metadata: dict[str, object]) -> str | None:
    category = str(metadata.get("resolved_category") or metadata.get("category") or "").strip()
    if not category:
        return None
    return category.replace("_", " ").title()


def build_fact_no_results_text(query_contract: QueryExecutionContract | None, *, locale: str = "en") -> str | None:
    """Return compact conversational no-results copy for fact queries when available."""
    if query_contract is None or query_contract.filters is None:
        return None

    tx_type = query_contract.filters.transaction_type
    counterparty = _first_filter_value(query_contract.filters.counterparty)
    time_range = query_contract.time_range
    today = lagos_today()
    if _uses_unbounded_fact_latest_window(query_contract):
        time_suffix = ""
    elif time_range is None:
        time_suffix = render_message("query.reply.no_result.time.period", locale)
    elif time_range.start == time_range.end == today:
        time_suffix = render_message("query.reply.no_result.time.today", locale)
    else:
        time_suffix = render_message("query.reply.no_result.time.period", locale)

    if tx_type == "credit" and counterparty:
        return _normalize_no_result_reply(
            render_message(
            "query.reply.no_result.credit_named",
            locale,
            {"counterparty": counterparty, "time_suffix": time_suffix},
            )
        )
    if tx_type == "debit" and counterparty:
        return _normalize_no_result_reply(
            render_message(
            "query.reply.no_result.debit_named",
            locale,
            {"counterparty": counterparty, "time_suffix": time_suffix},
            )
        )
    if tx_type == "credit":
        return _normalize_no_result_reply(
            render_message("query.reply.no_result.credit_generic", locale, {"time_suffix": time_suffix})
        )
    if tx_type == "debit":
        return _normalize_no_result_reply(
            render_message("query.reply.no_result.debit_generic", locale, {"time_suffix": time_suffix})
        )
    return None


def _uses_unbounded_fact_latest_window(query_contract: QueryExecutionContract) -> bool:
    if query_contract.answer_fact_field is None or query_contract.result_reference != "latest":
        return False
    time_range = query_contract.time_range
    if time_range is None:
        return True
    today = lagos_today()
    return time_range.end == today and (today - time_range.start).days >= QUERY_LIMITS["max_lookback_days"] - 1


def _normalize_no_result_reply(text: str) -> str:
    normalized = " ".join(text.split())
    return re.sub(r"\s+([.,!?])", r"\1", normalized)
