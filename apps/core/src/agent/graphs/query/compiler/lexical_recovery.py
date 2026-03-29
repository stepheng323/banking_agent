"""Lexical recovery and clarification-time helpers for query parsing."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from apps.core.src.agent.graphs.query.models import (
    QueryAggregation,
    QueryExtractionResult,
    QueryTimeRange,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.models.extraction import AmbiguityCode

MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
THIS_YEAR_TOKENS = {"this_year", "current_year", "thisyear", "currentyear"}
LAST_YEAR_TOKENS = {"last_year", "previous_year", "lastyear", "previousyear"}
_LIST_TIME_SCOPED_SINGULAR_RE = re.compile(
    r"\b(?:show|list|view|get|check|display|see)\b.*\b("
    r"today(?:'s)?|yesterday(?:'s)?|this week(?:'s)?|last week(?:'s)?|"
    r"this month(?:'s)?|last month(?:'s)?|this year(?:'s)?|last year(?:'s)?)\b.*\btransaction\b(?!s)",
    re.IGNORECASE,
)
_RECENT_LIST_QUERY_RE = re.compile(
    r"\brecent\b.*\b(transactions?|debits?|credits?|payments?)\b",
    re.IGNORECASE,
)
_EXPLICIT_TIME_RANGE_RE = re.compile(
    r"\b(today|yesterday|this week|last week|this month|last month|this year|last year|"
    r"last\s+\d{1,3}\s+(?:day|days|week|weeks|month|months)|"
    r"past\s+\d{1,3}\s+(?:day|days|week|weeks|month|months))\b",
    re.IGNORECASE,
)


def month_token(period: str | None) -> int | None:
    if not period:
        return None
    token = period.strip().lower().replace("-", "_").replace(" ", "_")
    return MONTH_NAME_TO_NUMBER.get(token)


def resolve_month_period_with_year_hint(period: str, *, today: date) -> TimeRange | None:
    normalized = period.strip().lower().replace("-", "_").replace(" ", "_")
    parts = [part for part in normalized.split("_") if part]
    if not parts:
        return None

    month_number: int | None = None
    for part in parts:
        month_number = MONTH_NAME_TO_NUMBER.get(part)
        if month_number is not None:
            break
    if month_number is None:
        return None

    if any(token in normalized for token in THIS_YEAR_TOKENS):
        year = today.year
    elif any(token in normalized for token in LAST_YEAR_TOKENS):
        year = today.year - 1
    else:
        year = today.year if month_number <= today.month else today.year - 1

    month_start = date(year, month_number, 1)
    month_end = date(year, month_number, monthrange(year, month_number)[1])
    if year == today.year and month_number == today.month:
        month_end = today
    return TimeRange(start=month_start, end=month_end, granularity="month")


def normalize_month_name_without_year(extraction: QueryExtractionResult) -> QueryExtractionResult:
    month_number = month_token(extraction.time_range.period)
    if month_number is None:
        return extraction

    extraction.time_range.reference_type = TimeReference.EXPLICIT
    extraction.time_range.days_back = None
    month_names = {
        period
        for period, number in MONTH_NAME_TO_NUMBER.items()
        if number == month_number
    }
    extraction.ambiguities = [
        ambiguity
        for ambiguity in extraction.ambiguities
        if not (
            ambiguity.code == AmbiguityCode.TIME_VAGUE
            and (
                "no year specified" in (ambiguity.context or "").lower()
                or any(name in (ambiguity.context or "").lower() for name in month_names)
            )
        )
    ]
    return extraction


def parse_amount_token(amount_text: str, suffix: str) -> float | None:
    try:
        amount = float(amount_text.replace(",", ""))
    except ValueError:
        return None

    suffix = suffix.lower()
    if suffix == "k":
        amount *= 1000
    elif suffix == "h":
        amount *= 100
    return amount if amount > 0 else None


def extract_beneficiary_query_amount_bounds(raw_query: str) -> tuple[float | None, float | None] | None:
    normalized = " ".join(raw_query.lower().strip().split())
    exact_match = re.search(
        r"\b(?:sent|pay|paid|transfer(?:red)?)\s+(?:money\s+)?(?:of\s+)?(?:about\s+)?(?:for\s+)?"
        r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKhH]?)\s+to\b",
        normalized,
    )
    if exact_match:
        amount = parse_amount_token(exact_match.group(1), exact_match.group(2))
        if amount is not None:
            return amount, amount

    lower_bound_match = re.search(
        r"\b(?:sent|pay|paid|transfer(?:red)?)\s+(?:money\s+)?"
        r"(?:(?:greater|more)\s+than|above|over|at\s+least|minimum(?:\s+of)?)\s+"
        r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKhH]?)\s+to\b",
        normalized,
    )
    if lower_bound_match:
        amount = parse_amount_token(lower_bound_match.group(1), lower_bound_match.group(2))
        if amount is not None:
            return amount, None

    upper_bound_match = re.search(
        r"\b(?:sent|pay|paid|transfer(?:red)?)\s+(?:money\s+)?"
        r"(?:(?:less|lower)\s+than|below|under|at\s+most|maximum(?:\s+of)?|up\s+to)\s+"
        r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKhH]?)\s+to\b",
        normalized,
    )
    if upper_bound_match:
        amount = parse_amount_token(upper_bound_match.group(1), upper_bound_match.group(2))
        if amount is not None:
            return None, amount

    return None


def extract_relative_time_range_from_query(raw_query: str) -> QueryTimeRange | None:
    normalized = " ".join(raw_query.lower().strip().split())
    explicit_tokens = (
        "today",
        "yesterday",
        "this week",
        "last week",
        "this month",
        "last month",
        "this year",
        "last year",
    )
    for token in explicit_tokens:
        if re.search(rf"\b{re.escape(token)}\b", normalized):
            return QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=token.replace(" ", "_"))

    match = re.search(r"\b(last|past)\s+(\d{1,3})\s+(day|days|week|weeks|month|months)\b", normalized)
    if not match:
        return None

    amount = max(1, int(match.group(2)))
    unit = match.group(3)
    if unit.startswith("week"):
        amount *= 7
    elif unit.startswith("month"):
        amount *= 30
    return QueryTimeRange(reference_type=TimeReference.EXPLICIT, days_back=max(amount - 1, 0))


def recover_known_fragile_query_shapes(extraction: QueryExtractionResult) -> QueryExtractionResult:
    raw_query = " ".join((extraction.raw_query or "").strip().lower().split())
    if not raw_query:
        return extraction

    extraction = normalize_recent_list_time_range(extraction, raw_query=raw_query)
    extraction = normalize_day_scoped_singular_list_query(extraction, raw_query=raw_query)

    people_query = any(
        cue in raw_query
        for cue in (
            "show people i sent",
            "show people i paid",
            "who did i send",
            "who have i sent",
            "who i sent",
        )
    )
    if not people_query:
        return extraction

    amount_bounds = extract_beneficiary_query_amount_bounds(raw_query)
    if amount_bounds is None and extraction.filters.min_amount is None and extraction.filters.max_amount is None:
        return extraction

    from apps.core.src.agent.graphs.query.models import ExtractionIntent

    extraction.intent = ExtractionIntent.BENEFICIARY_SUMMARY
    extraction.filters.transaction_type = "debit"
    if amount_bounds is not None:
        min_amount, max_amount = amount_bounds
        extraction.filters.min_amount = min_amount
        extraction.filters.max_amount = max_amount
    if extraction.aggregation is None:
        extraction.aggregation = QueryAggregation(type="sum", sort_by="count", limit=5)
    else:
        extraction.aggregation.type = extraction.aggregation.type or "sum"
        extraction.aggregation.sort_by = extraction.aggregation.sort_by or "count"
        extraction.aggregation.limit = extraction.aggregation.limit or 5
    if extraction.time_range.reference_type == TimeReference.UNSPECIFIED:
        recovered_time = extract_relative_time_range_from_query(raw_query)
        if recovered_time is not None:
            extraction.time_range = recovered_time
    return extraction


def normalize_recent_list_time_range(extraction: QueryExtractionResult, *, raw_query: str) -> QueryExtractionResult:
    list_shaped_intents = {
        "transaction_list",
        "single_transaction",
    }
    request_shape = extraction.request_shape.value if extraction.request_shape is not None else None
    if extraction.intent.value not in list_shaped_intents and request_shape != "list":
        return extraction
    if not _RECENT_LIST_QUERY_RE.search(raw_query):
        return extraction
    if extraction.time_range.reference_type == TimeReference.EXPLICIT:
        return extraction
    if _EXPLICIT_TIME_RANGE_RE.search(raw_query):
        recovered_time = extract_relative_time_range_from_query(raw_query)
        if recovered_time is not None:
            extraction.time_range = recovered_time
            extraction.ambiguities = [
                ambiguity
                for ambiguity in extraction.ambiguities
                if not (
                    ambiguity.code == AmbiguityCode.TIME_VAGUE
                    and (ambiguity.context or "").strip().lower() in {"recent", "recently", "that time"}
                )
            ]
            return extraction

    extraction.time_range = QueryTimeRange(
        reference_type=TimeReference.EXPLICIT,
        period="recent_30_days",
        days_back=30,
    )
    extraction.ambiguities = [
        ambiguity
        for ambiguity in extraction.ambiguities
        if not (
            ambiguity.code == AmbiguityCode.TIME_VAGUE
            and (ambiguity.context or "").strip().lower() in {"recent", "recently", "that time"}
        )
    ]
    return extraction


def normalize_day_scoped_singular_list_query(extraction: QueryExtractionResult, *, raw_query: str) -> QueryExtractionResult:
    match = _LIST_TIME_SCOPED_SINGULAR_RE.search(raw_query)
    if not match:
        return extraction

    period = match.group(1).replace("'s", "").replace(" ", "_")
    from apps.core.src.agent.graphs.query.models import ExtractionIntent

    extraction.intent = ExtractionIntent.TRANSACTION_LIST
    extraction.request_shape = None
    extraction.fact_query_kind = None
    extraction.answer_fact_field = None
    extraction.result_limit = None
    extraction.result_reference = None
    extraction.time_range = QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=period)
    return extraction


def parse_clarification_time_range(
    message: str,
    *,
    today: date,
) -> QueryTimeRange | None:
    normalized = " ".join(message.lower().strip().split()).rstrip("?.!,")
    mapping = {
        "today": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        "yesterday": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday", days_back=1),
        "this week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
        "last week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        "this month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        "last month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_month"),
        "this year": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_year"),
        "last year": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_year"),
        "all time": QueryTimeRange(reference_type=TimeReference.ALL_TIME),
        "ever": QueryTimeRange(reference_type=TimeReference.ALL_TIME),
    }
    if normalized in mapping:
        return mapping[normalized]

    if month_token(normalized) is not None or resolve_month_period_with_year_hint(normalized, today=today) is not None:
        return QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=normalized.replace(" ", "_"))

    match = re.fullmatch(r"(last|past)\s+(\d{1,3})\s+(day|days|week|weeks|month|months)", normalized)
    if not match:
        return None

    amount = max(1, int(match.group(2)))
    unit = match.group(3)
    if unit.startswith("week"):
        amount *= 7
    elif unit.startswith("month"):
        amount *= 30
    return QueryTimeRange(reference_type=TimeReference.EXPLICIT, days_back=amount)
