"""Conversational continuity for query follow-ups and drill-down.

Handles:
- "What about last month?" (time delta)
- "Only credits" (filter delta)
- "Tell me more about the 150k one" (drill-down)

Uses LLM for multilingual continuation classification.
"""

import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Literal, cast

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.prompts import CONTINUATION_CLASSIFIER_PROMPT
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

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


class ProposedTimeRange(BaseModel):
    """Loose time range for classification (handles missing/partial LLM output)."""

    start: date | None = None
    end: date | None = None
    granularity: Literal["day", "week", "month"] | None = None


class ContinuationClassification(BaseModel):
    """LLM output for continuation classification."""

    continuation_type: Literal[
        "show_more",
        "time_delta",
        "filter_delta",
        "expand",
        "drill_down",
        "recipient_drill_down",
        "aggregate",
        "unclear",
        "end_session",
        "new_query",
    ] = Field(description="Type of continuation the user is requesting")

    confidence: float | None = Field(default=None, description="Confidence in classification (0.0-1.0)")
    reason: str | None = Field(default=None, description="Short reason for the classification decision")
    is_new_query_override: bool | None = Field(
        default=None, description="Explicit signal to treat as a new query despite active session"
    )
    restates_query: bool | None = Field(
        default=None, description="True if the user restated a full query rather than a follow-up"
    )
    delta_type: Literal["filter", "time", "limit", "reference", "none"] | None = Field(
        default=None, description="Primary follow-up change type when continuation_type is filter_delta/time_delta"
    )

    time_range: ProposedTimeRange | None = Field(default=None, description="Resolved date range if time_delta")

    filters: Filters | None = Field(default=None, description="Filter modifications if filter_delta")
    result_limit: int | None = Field(default=None, description="Max results to return if user specifies a count")
    result_reference: Literal["latest", "oldest"] | None = Field(
        default=None, description="Relative positioning if user asks for most recent/oldest"
    )

    drill_down_index: int | None = Field(
        default=None, description="Index of item user is referencing (0-indexed) if drill_down"
    )

    drill_down_action: Literal["view_details", "get_receipt", "report_issue", "re_transfer"] | None = Field(
        default=None, description="What user wants to do with the item if drill_down"
    )

    end_session_response: str | None = Field(
        default=None, description="Witty goodbye response in matching language if end_session"
    )

    recipient_name: str | None = Field(
        default=None, description="Recipient name if recipient_drill_down (e.g., 'Uber', 'Mum')"
    )


class ContinuationClassifier:
    """LLM-based continuation classifier for multilingual support."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.structured_llm = cast(Any, llm).with_structured_output(ContinuationClassification)

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

    def _guardrail_classify(
        self,
        *,
        message: str,
        today: str,
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

    async def classify(
        self,
        message: str,
        has_active_session: bool,
        today: str,
        items: list[QueryResultItem] | None = None,
        surface: ResultSurface | None = None,
        language: str = "en",
    ) -> tuple[str, dict[str, Any]]:
        """
        Classify a user message as a continuation type.

        Args:
            message: User's message
            has_active_session: Whether there's an active query session
            today: Today's date in YYYY-MM-DD format
            items: Optional list of items for drill-down resolution
            surface: Optional active result surface context

        Returns:
            Tuple of (continuation_type, extracted_data)
        """
        if not has_active_session:
            return ContinuationType.NEW_QUERY, {}

        guarded = self._guardrail_classify(message=message, today=today, surface=surface, language=language)
        if guarded is not None:
            continuation_type, guarded_data = guarded
            logger.info("continuation_guardrail_hit", type=continuation_type, reason=guarded_data.get("reason"))
            return continuation_type, guarded_data

        try:
            items_section = ""
            if items:
                items_list = "\n".join(
                    f"{i}: {item.description} - ₦{item.amount:,.2f} ({item.date})" for i, item in enumerate(items)
                )
                items_section = f"\n{render_message('query.continuity.prompt_items_header', language)}\n{items_list}\n"

            # Format surface context
            surface_type = "unknown"
            surface_context = "none"
            if surface:
                surface_type = surface.type.value
                if surface.type == SurfaceType.BREAKDOWN:
                    keys = [item.get("key", "") for item in surface.items[:5]]
                    surface_context = render_message(
                        "query.continuity.prompt_surface_top_keys",
                        language,
                        {"keys": ", ".join(keys)},
                    )
                elif surface.type == SurfaceType.LIST:
                    surface_context = render_message(
                        "query.continuity.prompt_surface_showing_items",
                        language,
                        {"count": len(surface.items)},
                    )

            prompt = CONTINUATION_CLASSIFIER_PROMPT.format(
                today=today,
                message=message,
                items_section=items_section,
                surface_type=surface_type,
                surface_context=surface_context,
            )

            result: ContinuationClassification = await self.structured_llm.ainvoke(prompt)

            data: dict[str, Any] = {}
            if result.confidence is not None:
                data["confidence"] = result.confidence
            if result.reason:
                data["reason"] = result.reason
            if result.is_new_query_override is not None:
                data["is_new_query_override"] = result.is_new_query_override
            if result.restates_query is not None:
                data["restates_query"] = result.restates_query
            if result.delta_type is not None:
                data["delta_type"] = result.delta_type

            if result.continuation_type == "time_delta" and result.time_range:
                data["time_range"] = result.time_range

            elif result.continuation_type == "filter_delta" and result.filters:
                data["filters"] = result.filters
                if result.result_limit is not None:
                    data["result_limit"] = result.result_limit
                if result.result_reference is not None:
                    data["result_reference"] = result.result_reference

            elif result.continuation_type == "filter_delta":
                if result.result_limit is not None:
                    data["result_limit"] = result.result_limit
                if result.result_reference is not None:
                    data["result_reference"] = result.result_reference

            elif result.continuation_type == "drill_down":
                # Default to index 0 if not specified (common when only 1 item shown)
                drill_idx = result.drill_down_index if result.drill_down_index is not None else 0
                data["drill_down_index"] = drill_idx
                data["drill_down_action"] = result.drill_down_action or "view_details"

            elif result.continuation_type == "end_session":
                data["end_session_response"] = result.end_session_response or render_message(
                    "query.session.you_are_welcome", language
                )

            logger.info("continuation_classified", type=result.continuation_type)
            return result.continuation_type, data

        except Exception as e:
            logger.error("continuation_classification_error", error=str(e))
            return ContinuationType.NEW_QUERY, {}


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
