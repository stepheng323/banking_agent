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
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from apps.chat.src.agent.graphs.query.services.continuity import build_soft_clarification
from apps.chat.src.agent.graphs.query.services.contracts import build_focus_referent
from apps.chat.src.agent.graphs.query.utils.timezone import lagos_today
from shared.i18n import render_message

FactKind = Literal["date", "counterparty", "amount", "bank"]
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
    result_reference: Literal["latest", "oldest"] | None


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
        result_reference=query_contract.result_reference if query_contract is not None else None,
    )
    primary, used_fields = _compose_direct_reply(fact, locale=locale)
    secondary = _build_evidence_line(item, query_contract=query_contract, used_fields=used_fields)
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


def _normalize_fact_kind(fact_field: str) -> FactKind:
    if fact_field in {"date", "counterparty", "amount", "bank"}:
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
    if locale == "en" and fact.result_reference == "latest":
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
) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
    counterparty = _counterparty_label(item, query_contract=query_contract)
    date_text = item.date.strftime("%b %d")
    amount = f"₦{abs(float(item.amount)):,.0f}"

    parts: list[str] = []
    if "amount" not in used_fields:
        parts.append(amount)
    if "date" not in used_fields:
        parts.append(date_text)
    if "counterparty" not in used_fields and counterparty:
        parts.append(counterparty)
    if "bank" not in used_fields and bank_name:
        parts.append(bank_name)

    return " • ".join(parts[:3]) or None


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
