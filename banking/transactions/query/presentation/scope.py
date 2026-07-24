"""Shared presentation-scope helpers for query headings and summaries."""

from __future__ import annotations

from datetime import date, timedelta

from banking.presentation.formatters.currency import format_naira as _format_naira
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.utils.timezone import lagos_today


def format_naira(amount: float) -> str:
    return _format_naira(amount, absolute=True)


def period_label(time_range: object, locale: str = "en") -> str | None:
    if not isinstance(time_range, object) or time_range is None:
        return None

    start = getattr(time_range, "start", None)
    end = getattr(time_range, "end", None)
    if not isinstance(start, date) or not isinstance(end, date):
        return None

    today = lagos_today()
    if start == end == today:
        return render_message("query.format.heading_period_today", locale)

    this_month_start = today.replace(day=1)
    if start == this_month_start and end == today:
        return render_message("query.format.heading_period_this_month", locale)

    prev_month_end = this_month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    if start == prev_month_start and end == prev_month_end:
        return prev_month_start.strftime("%B")

    return render_message(
        "query.format.heading_period_range",
        locale,
        {"start": _format_date(start), "end": _format_date(end)},
    )


def build_amount_scope_label(query_request: QueryRequest | None, locale: str = "en") -> str | None:
    if query_request is None or query_request.filters is None:
        return None

    min_amount = query_request.filters.min_amount
    max_amount = query_request.filters.max_amount
    if min_amount is None and max_amount is None:
        return None

    if locale != "en":
        if min_amount is not None and max_amount is not None and float(min_amount) == float(max_amount):
            return format_naira(float(min_amount))
        if min_amount is not None and max_amount is None:
            return f"{format_naira(float(min_amount))}+"
        if max_amount is not None and min_amount is None:
            return f"≤ {format_naira(float(max_amount))}"
        if min_amount is not None and max_amount is not None:
            return f"{format_naira(float(min_amount))}–{format_naira(float(max_amount))}"
        return None

    if min_amount is not None and max_amount is not None and float(min_amount) == float(max_amount):
        return format_naira(float(min_amount))
    if min_amount is not None and max_amount is None:
        return f"Over {format_naira(float(min_amount))}"
    if max_amount is not None and min_amount is None:
        return f"Under {format_naira(float(max_amount))}"
    if min_amount is not None and max_amount is not None:
        return f"{format_naira(float(min_amount))}–{format_naira(float(max_amount))}"
    return None


def build_transaction_heading(query_request: QueryRequest | None, locale: str = "en") -> str | None:
    if query_request is None:
        return None

    filters = query_request.filters
    tx_type = filters.transaction_type if filters else None
    categories = filters.category if filters and filters.category else []
    counterparties = filters.counterparty if filters and filters.counterparty else []
    counterparty = _first_display_filter_value(counterparties)

    if counterparty:
        if tx_type == "debit":
            heading = f"*Payments to {counterparty}*"
        elif tx_type == "credit":
            heading = f"*Credits from {counterparty}*"
        else:
            heading = f"*Transactions with {counterparty}*"
    elif len(categories) == 1 and tx_type == "debit":
        heading = f"*{categories[0].strip().title()} Spending*"
    elif tx_type == "credit":
        heading = "*Credit Transactions*"
    elif tx_type == "debit":
        heading = "*Debit Transactions*"
    elif len(categories) == 1:
        heading = f"*{categories[0].strip().title()} Transactions*"
    else:
        heading = "*Transactions*"

    qualifiers = _collect_scope_qualifiers(query_request, locale=locale, include_counterparty=False)
    return _join_heading(heading, qualifiers)


def build_breakdown_heading(
    query_request: QueryRequest | None,
    *,
    group_by: str | None,
    locale: str = "en",
    fallback_summary: str | None = None,
) -> str:
    if query_request is None or locale != "en":
        return fallback_summary or "Breakdown"

    tx_type = query_request.filters.transaction_type if query_request.filters else None
    group_label = {
        "account": "account",
        "category": "category",
        "merchant": "merchant",
        "day": "day",
        "transaction_type": "transaction type",
    }.get(group_by or "", group_by or "category")

    if tx_type == "debit":
        base = f"Spending by {group_label}"
    elif tx_type == "credit" and group_by == "account":
        base = "Money came in by account"
    elif tx_type == "credit":
        base = f"Income by {group_label}"
    else:
        base = f"Breakdown by {group_label}"

    qualifiers = _collect_scope_qualifiers(query_request, locale=locale, include_counterparty=True)
    return _join_heading(base, qualifiers)


def build_beneficiary_summary_header(
    query_request: QueryRequest | None,
    *,
    ranking_heading: str,
    timeframe: str,
    locale: str = "en",
) -> str:
    target_type = query_request.filters.transaction_type if query_request and query_request.filters else "debit"
    entity_label = "Senders" if target_type == "credit" else "Recipients"

    if query_request is None:
        return f"*{ranking_heading} {entity_label}* — {timeframe}"

    amount_label = build_amount_scope_label(query_request, locale=locale)
    if locale == "en" and amount_label:
        verb = "received" if target_type == "credit" else "sent"
        if (
            query_request.filters
            and query_request.filters.max_amount is not None
            and query_request.filters.min_amount is None
        ):
            if target_type == "credit":
                heading = f"{entity_label} I {verb} under {format_naira(float(query_request.filters.max_amount))} from"
            else:
                heading = f"Recipients I sent under {format_naira(float(query_request.filters.max_amount))} to"
        elif (
            query_request.filters
            and query_request.filters.min_amount is not None
            and query_request.filters.max_amount is None
        ):
            if target_type == "credit":
                heading = f"{entity_label} I {verb} over {format_naira(float(query_request.filters.min_amount))} from"
            else:
                heading = f"Recipients I sent over {format_naira(float(query_request.filters.min_amount))} to"
        elif (
            query_request.filters
            and query_request.filters.min_amount is not None
            and query_request.filters.max_amount is not None
            and float(query_request.filters.min_amount) == float(query_request.filters.max_amount)
        ):
            if target_type == "credit":
                heading = f"{entity_label} I {verb} {format_naira(float(query_request.filters.min_amount))} from"
            else:
                heading = f"Recipients I sent {format_naira(float(query_request.filters.min_amount))} to"
        else:
            if target_type == "credit":
                heading = f"{entity_label} I {verb} within that amount range from"
            else:
                heading = "Recipients I sent within that amount range to"
        base = f"*{heading}*"
    else:
        base = f"*{ranking_heading} {entity_label}*"

    qualifiers: list[str] = []
    account_filter = (query_request.filters.account_filter or "").strip() if query_request.filters else ""
    if account_filter:
        qualifiers.append(account_filter)
    qualifiers.append(timeframe)
    return _join_heading(base, qualifiers)


def _collect_scope_qualifiers(
    query_request: QueryRequest,
    *,
    locale: str,
    include_counterparty: bool,
) -> list[str]:
    qualifiers: list[str] = []
    filters = query_request.filters

    if include_counterparty and filters and filters.counterparty and locale == "en":
        counterparty = _first_display_filter_value(filters.counterparty)
        if counterparty:
            qualifiers.append(f"With {counterparty}")

    amount_label = build_amount_scope_label(query_request, locale=locale)
    if amount_label:
        qualifiers.append(amount_label)

    account_filter = (filters.account_filter or "").strip() if filters else ""
    if account_filter:
        qualifiers.append(account_filter)

    label = period_label(query_request.time_range, locale=locale)
    if label:
        qualifiers.append(label)

    return qualifiers


def _join_heading(base: str, qualifiers: list[str]) -> str:
    filtered = [qualifier for qualifier in qualifiers if qualifier]
    if not filtered:
        return base
    return f"{base} — {' — '.join(filtered)}"


def _first_display_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            if cleaned == cleaned.lower():
                return cleaned.title()
            return cleaned
    return None


def _format_date(value: date) -> str:
    return value.strftime("%b %d").replace(" 0", " ")
