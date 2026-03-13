"""Deterministic continuity helpers for query follow-ups and drill-down."""

import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from shared.i18n import render_message

_SHOW_MORE_EXACT = {
    "more",
    "next",
    "continue",
    "show more",
    "next page",
    "another page",
    "others",
    "any others",
    "any other ones",
}
_END_SESSION_PATTERNS = (
    r"\bthanks?\b",
    r"\bthank you\b",
    r"\bi'?m done\b",
    r"\bdone\b",
    r"\be se\b",
    r"\bese\b",
)
_EXPAND_EXACT = {
    "show transactions",
    "show my transactions",
    "list transactions",
    "show the items",
    "which ones",
    "show them",
    "list them",
}
_RETRANSFER_PHRASES = ("resend", "repeat", "send again", "do it again")
_AGGREGATE_PATTERNS = (
    r"\bhow much\b",
    r"\btotal\s*(spending|spent|received)?\b",
    r"\bsum\s*(it|them)?\s*(up)?\b",
    r"\bhow many\b",
)
_RECIPIENT_RANKING_PATTERNS = (
    r"\bwho did i (send money|transfer) to the most\b",
    r"\bwho do i (send money|transfer) to the most\b",
    r"\btop recipients?\b",
    r"\bmost frequent recipients?\b",
    r"\bmost frequent transfer to\b",
    r"\brecipient ranking\b",
)


class ContinuationType:
    """Types of follow-up continuations."""

    SHOW_MORE = "show_more"
    TIME_DELTA = "time_delta"
    FILTER_DELTA = "filter_delta"
    EXPAND = "expand"
    DRILL_DOWN = "drill_down"
    RECIPIENT_DRILL_DOWN = "recipient_drill_down"
    AGGREGATE = "aggregate"
    UNCLEAR = "unclear"
    END_SESSION = "end_session"
    NEW_QUERY = "new_query"


class ContinuationClassifier:
    """Deterministic continuation helpers used by the query reasoner."""

    def __init__(self, llm: Any):
        del llm

    @staticmethod
    def _normalize_message(message: str) -> str:
        return " ".join(message.lower().strip().split())

    @staticmethod
    def _parse_today(today: str) -> date:
        try:
            return date.fromisoformat(today)
        except Exception:
            return date.today()

    @staticmethod
    def _strip_trailing_punctuation(text: str) -> str:
        return re.sub(r"[?.!,]+$", "", text).strip()

    @staticmethod
    def _normalize_recipient_name(value: str) -> str:
        cleaned = " ".join(value.strip().split()).rstrip(".,;:!?").strip()
        return cleaned.casefold()

    def _resolve_beneficiary_summary_recipient_reply(
        self,
        message: str,
        *,
        items: list[QueryResultItem] | None,
        surface: ResultSurface | None,
    ) -> str | None:
        if not surface or surface.type != SurfaceType.SUMMARY:
            return None
        if not isinstance(surface.context, dict) or surface.context.get("view") != "beneficiary_summary":
            return None

        candidate = self._strip_trailing_punctuation(" ".join(message.strip().split()))
        if not candidate:
            return None

        normalized_candidate = self._normalize_recipient_name(candidate)
        if not normalized_candidate:
            return None

        for item in items or []:
            item_name = str(item.description or "").strip()
            if not item_name:
                continue
            if self._normalize_recipient_name(item_name) == normalized_candidate:
                return item_name

        return None

    def _resolve_time_delta_range(self, message: str, *, today: date) -> TimeRange | None:
        normalized = self._strip_trailing_punctuation(self._normalize_message(message))
        candidate = normalized
        for prefix in ("what about ", "how about ", "for ", "in "):
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix) :].strip()
                break

        if candidate == "today":
            return TimeRange(start=today, end=today, granularity="day")
        if candidate == "yesterday":
            yesterday = today - timedelta(days=1)
            return TimeRange(start=yesterday, end=yesterday, granularity="day")
        if candidate in {"this week", "current week"}:
            week_start = today - timedelta(days=today.weekday())
            return TimeRange(start=week_start, end=today, granularity="week")
        if candidate in {"last week", "previous week"}:
            this_week_start = today - timedelta(days=today.weekday())
            week_end = this_week_start - timedelta(days=1)
            week_start = week_end - timedelta(days=6)
            return TimeRange(start=week_start, end=week_end, granularity="week")
        if candidate in {"this month", "current month"}:
            month_start = date(today.year, today.month, 1)
            return TimeRange(start=month_start, end=today, granularity="month")
        if candidate in {"last month", "previous month"}:
            year = today.year
            month = today.month - 1
            if month == 0:
                month = 12
                year -= 1
            month_end = monthrange(year, month)[1]
            return TimeRange(start=date(year, month, 1), end=date(year, month, month_end), granularity="month")
        if candidate in {"this year", "current year"}:
            return TimeRange(start=date(today.year, 1, 1), end=today, granularity="month")
        if candidate in {"last year", "previous year"}:
            year = today.year - 1
            return TimeRange(start=date(year, 1, 1), end=date(year, 12, 31), granularity="month")

        match = re.match(r"^(last|past)\s+(\d{1,3})\s+days?$", candidate)
        if match:
            days = max(1, int(match.group(2)))
            start = today - timedelta(days=days - 1)
            return TimeRange(start=start, end=today, granularity="day")

        return None

    def _resolve_tx_type_filter(self, message: str) -> Filters | None:
        normalized = self._strip_trailing_punctuation(self._normalize_message(message))

        debit_forms = r"(debit|debits)"
        credit_forms = r"(credit|credits)"

        debit_patterns = (
            rf"^(only|just)\s+{debit_forms}$",
            rf"^{debit_forms}\s+only$",
            rf"^(is there any|any|what about|how about)\s+{debit_forms}$",
            rf"^show\s+{debit_forms}$",
        )
        credit_patterns = (
            rf"^(only|just)\s+{credit_forms}$",
            rf"^{credit_forms}\s+only$",
            rf"^(is there any|any|what about|how about)\s+{credit_forms}$",
            rf"^show\s+{credit_forms}$",
        )

        if any(re.match(pattern, normalized) for pattern in debit_patterns):
            return Filters(transaction_type="debit")
        if any(re.match(pattern, normalized) for pattern in credit_patterns):
            return Filters(transaction_type="credit")
        return None

    @staticmethod
    def _is_recipient_ranking_request(message: str) -> bool:
        normalized = " ".join(message.lower().split())
        if not normalized:
            return False
        return any(re.search(pattern, normalized) for pattern in _RECIPIENT_RANKING_PATTERNS)

    def _guardrail_classify(
        self,
        *,
        message: str,
        today: str,
        items: list[QueryResultItem] | None,
        surface: ResultSurface | None,
        language: str,
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = self._normalize_message(message)
        if not normalized:
            return None

        parsed_today = self._parse_today(today)
        time_delta_range = self._resolve_time_delta_range(message, today=parsed_today)
        if time_delta_range is not None:
            return ContinuationType.TIME_DELTA, {
                "confidence": 0.98,
                "reason": "deterministic_time_delta",
                "delta_type": "time",
                "time_range": time_delta_range,
            }

        tx_type_filters = self._resolve_tx_type_filter(message)
        if tx_type_filters is not None:
            return ContinuationType.FILTER_DELTA, {
                "confidence": 0.98,
                "reason": "deterministic_tx_type_filter",
                "delta_type": "filter",
                "filters": tx_type_filters,
            }

        if normalized in _SHOW_MORE_EXACT:
            return ContinuationType.SHOW_MORE, {"confidence": 1.0, "reason": "deterministic_show_more"}

        if any(re.search(pattern, normalized) for pattern in _END_SESSION_PATTERNS):
            return ContinuationType.END_SESSION, {
                "confidence": 1.0,
                "reason": "deterministic_end_session",
                "end_session_response": render_message("query.session.you_are_welcome", language),
            }

        recipient_name = self._resolve_beneficiary_summary_recipient_reply(message, items=items, surface=surface)
        if recipient_name:
            return ContinuationType.RECIPIENT_DRILL_DOWN, {
                "confidence": 0.99,
                "reason": "deterministic_recipient_drill_down",
                "delta_type": "filter",
                "recipient_name": recipient_name,
            }

        if self._is_recipient_ranking_request(message):
            return ContinuationType.NEW_QUERY, {
                "confidence": 0.99,
                "reason": "deterministic_recipient_ranking_new_query",
                "is_new_query_override": True,
                "restates_query": True,
            }

        if surface and surface.type in {SurfaceType.SUMMARY, SurfaceType.BREAKDOWN} and normalized in _EXPAND_EXACT:
            return ContinuationType.EXPAND, {"confidence": 0.98, "reason": "deterministic_expand"}

        if (
            any(phrase in normalized for phrase in _RETRANSFER_PHRASES)
            and surface
            and surface.type
            in {
                SurfaceType.SINGLE_ITEM,
                SurfaceType.LIST,
            }
        ):
            return ContinuationType.DRILL_DOWN, {
                "confidence": 0.98,
                "reason": "deterministic_retransfer",
                "drill_down_index": 0,
                "drill_down_action": "re_transfer",
            }

        if any(re.search(pattern, normalized) for pattern in _AGGREGATE_PATTERNS):
            return ContinuationType.AGGREGATE, {"confidence": 0.95, "reason": "deterministic_aggregate"}

        return None


def apply_filter_delta(
    original_query: NormalizedQuery,
    filters: Filters,
) -> NormalizedQuery:
    """
    Apply a filter delta to an existing query.

    Args:
        original_query: The original normalized query
        filters: Filter modifications to apply

    Returns:
        Modified NormalizedQuery
    """
    query_dict = original_query.model_dump()
    existing_filters = query_dict.get("filters") or {}

    new_filters = filters.model_dump(exclude_none=True)
    for key, value in new_filters.items():
        if key == "exclude" and existing_filters.get("exclude"):
            existing_filters["exclude"] = existing_filters["exclude"] + value
        else:
            existing_filters[key] = value

    query_dict["filters"] = existing_filters
    return NormalizedQuery.model_validate(query_dict)


def apply_time_delta(
    original_query: NormalizedQuery,
    time_range: TimeRange,
) -> NormalizedQuery:
    """
    Apply a time range change to an existing query.

    Args:
        original_query: The original normalized query
        time_range: New time range

    Returns:
        Modified NormalizedQuery
    """
    query_dict = original_query.model_dump()
    query_dict["time_range"] = time_range.model_dump()
    return NormalizedQuery.model_validate(query_dict)


def build_soft_clarification(items: list[QueryResultItem], context: str = "", locale: str = "en") -> str:
    """Build a graceful clarification message without resetting context.

    Args:
        items: List of items to offer as options
        context: Optional context string (e.g., "Which transaction")

    Returns:
        Formatted clarification message with numbered options
    """
    if not items:
        return render_message("query.clarify.unsure_rephrase", locale)

    context_suffix = render_message("query.clarify.context_suffix", locale, {"context": context}) if context else ""
    lines = [render_message("query.clarify.which_one", locale, {"context_suffix": context_suffix})]
    lines.append("")
    lines.append(render_message("query.clarify.are_you_referring", locale))

    for i, item in enumerate(items[:5], 1):  # Max 5 options
        amount = f"₦{abs(item.amount):,.0f}" if item.amount else ""
        lines.append(
            render_message(
                "query.clarify.option_line",
                locale,
                {"index": i, "amount": amount, "description": item.description[:30]},
            )
        )

    lines.append("")
    lines.append(render_message("query.clarify.reply_number_or_rephrase", locale))

    return "\n".join(lines)


def get_recovery_message(locale: str = "en") -> str:
    """Get the recovery message for total failure scenario (3+ clarification attempts)."""
    return render_message("query.clarify.recovery_options", locale)


def should_offer_recovery(clarification_attempts: int, max_attempts: int = 3) -> bool:
    """Check if we should offer recovery options based on attempt count."""
    return clarification_attempts >= max_attempts
