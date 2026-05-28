"""No-result copy for fact-shaped transaction queries."""

import re

from apps.chat.src.agent.workers.query.capabilities import QUERY_LIMITS
from apps.chat.src.agent.workers.query.models.domain import QueryExecutionContract
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from shared.i18n.renderer import render_message


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


def _first_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _normalize_no_result_reply(text: str) -> str:
    normalized = " ".join(text.split())
    return re.sub(r"\s+([.,!?])", r"\1", normalized)
