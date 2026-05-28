"""User-facing aggregate scope copy for query continuations."""

from __future__ import annotations

from apps.chat.src.agent.workers.query.models.domain import QueryExecutionContract, QueryResult
from apps.chat.src.agent.workers.query.presentation.scope import (
    build_transaction_heading,
    format_naira,
    period_label,
)
from shared.i18n.renderer import render_message


def build_aggregate_scope_reply(
    *,
    session_query_contract: QueryExecutionContract | None,
    query_result: QueryResult | None,
    locale: str,
) -> str:
    if session_query_contract is None:
        return render_message("query.clarify.unsure_rephrase", locale)

    scope_heading = build_transaction_heading(session_query_contract, locale=locale)
    if scope_heading:
        scope_heading = scope_heading.strip("*")
    else:
        scope_heading = render_message("query.common.transaction", locale).title()

    total_amount = None
    total_count = None
    if query_result is not None and query_result.items:
        total_amount = sum(abs(float(item.amount or 0.0)) for item in query_result.items)
        total_count = len(query_result.items)

    total_text = format_naira(total_amount) if total_amount is not None else None
    period_text = period_label(session_query_contract.time_range, locale=locale)
    period_suffix = f" ({period_text})" if period_text else ""
    total_suffix = (
        render_message("query.reply.aggregate.total_suffix", locale, {"total": total_text})
        if total_text is not None
        else ""
    )

    primary = render_message(
        "query.reply.aggregate.scope",
        locale,
        {
            "scope_label": scope_heading,
            "period_suffix": period_suffix,
            "total_suffix": total_suffix,
        },
    ).strip()

    if total_count is None:
        return primary

    hint = render_message(
        "query.reply.aggregate.scope_hint",
        locale,
        {
            "count": total_count,
            "transaction_label": (
                render_message("query.analytics.transaction_singular", locale)
                if total_count == 1
                else render_message("query.analytics.transaction_plural", locale)
            ),
        },
    )
    return f"{primary}\n\n{hint}"


__all__ = ["build_aggregate_scope_reply"]
