"""Grounded continuity helpers for query session exits and surface actions."""

import re
from typing import Any

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.shared.query_contracts import SurfaceView, SurfaceViewMode
from shared.i18n import render_message

_END_SESSION_PATTERNS = (
    r"\bthanks?\b",
    r"\bthank you\b",
    r"\bi'?m done\b",
    r"\bdone\b",
    r"\be se\b",
    r"\bese\b",
)
_RETRANSFER_PHRASES = ("resend", "repeat", "send again", "do it again")

_TIME_DELTA_RE = re.compile(
    r"^(?:what about|how about|and|show me?|for|only)\s+"
    r"(today|yesterday|last week|this week|last month|this month|"
    r"last year|this year|january|february|march|april|may|june|"
    r"july|august|september|october|november|december)"
    r"(?:\?|!|\.)?$",
    re.IGNORECASE,
)
_AGGREGATE_PATTERNS = (
    "total",
    "how much total",
    "what's the total",
    "whats the total",
    "sum it up",
    "sum",
    "how much in total",
    "wetin be total",
    "nawa be total",
    "lapapo meloo",
)
_FILTER_DELTA_RE = re.compile(
    r"^(?:what about|how about|show me?|and)\s+(credit|debit)s?(?:\?|!|\.)?$",
    re.IGNORECASE,
)
_BENEFICIARY_FACT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwhen\b|\bwhat date\b", "date"),
    (r"\bwhich bank\b|\bwhat bank\b", "bank"),
    (r"\bhow much\b|\bwhat(?:'s| is)? the amount\b|\bamount\b", "amount"),
)


class ContinuationClassifier:
    """Deterministic continuation helpers used by the query reasoner."""

    @staticmethod
    def _surface_mode(surface_view: SurfaceView | None) -> SurfaceViewMode | None:
        return surface_view.mode if surface_view is not None else None

    @staticmethod
    def _surface_view_name(surface_view: SurfaceView | None) -> str | None:
        if surface_view is not None and isinstance(surface_view.context, dict):
            view = surface_view.context.get("view")
            if isinstance(view, str):
                return view
        return None

    @staticmethod
    def _normalize_message(message: str) -> str:
        return " ".join(message.lower().strip().split())

    @staticmethod
    def _strip_trailing_punctuation(text: str) -> str:
        return re.sub(r"[?.!,]+$", "", text).strip()

    @staticmethod
    def _normalize_recipient_name(value: str) -> str:
        cleaned = " ".join(value.strip().split()).rstrip(".,;:!?").strip()
        return cleaned.casefold()

    @staticmethod
    def _tokenize_recipient_name(value: str) -> list[str]:
        normalized = re.sub(r"'s\b", "", value.casefold())
        return [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 3]

    def _resolve_beneficiary_summary_fact_field(self, message: str) -> str | None:
        normalized = self._normalize_message(message)
        for pattern, fact_field in _BENEFICIARY_FACT_PATTERNS:
            if re.search(pattern, normalized):
                return fact_field
        return None

    def _resolve_beneficiary_summary_recipient_reply(
        self,
        message: str,
        *,
        items: list[QueryResultItem] | None,
        surface_view: SurfaceView | None,
    ) -> str | None:
        surface_mode = self._surface_mode(surface_view)
        if surface_mode != SurfaceViewMode.GROUPED_SUMMARY:
            return None
        if self._surface_view_name(surface_view) != "beneficiary_summary":
            return None

        candidate = self._strip_trailing_punctuation(" ".join(message.strip().split()))
        if not candidate:
            return None

        normalized_candidate = self._normalize_recipient_name(candidate)
        if not normalized_candidate:
            return None

        token_matches: list[tuple[int, str]] = []
        for item in items or []:
            item_name = str(item.description or "").strip()
            if not item_name:
                continue
            if self._normalize_recipient_name(item_name) == normalized_candidate:
                return item_name
            item_tokens = self._tokenize_recipient_name(item_name)
            overlap = [token for token in item_tokens if re.search(rf"\b{re.escape(token)}\b", normalized_candidate)]
            if overlap:
                token_matches.append((len(max(overlap, key=len)), item_name))

        if len(token_matches) == 1:
            return token_matches[0][1]
        if token_matches:
            token_matches.sort(key=lambda entry: entry[0], reverse=True)
            if token_matches[0][0] > token_matches[1][0]:
                return token_matches[0][1]

        return None

    def _guardrail_classify(
        self,
        *,
        message: str,
        items: list[QueryResultItem] | None,
        surface_view: SurfaceView | None,
        language: str,
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = self._strip_trailing_punctuation(self._normalize_message(message))
        if not normalized:
            return None

        if any(re.search(pattern, normalized) for pattern in _END_SESSION_PATTERNS):
            return "end_session", {
                "confidence": 1.0,
                "reason": "deterministic_end_session",
                "end_session_response": render_message("query.session.you_are_welcome", language),
            }

        recipient_name = self._resolve_beneficiary_summary_recipient_reply(
            message,
            items=items,
            surface_view=surface_view,
        )
        if recipient_name:
            fact_field = self._resolve_beneficiary_summary_fact_field(message)
            return "recipient_drill_down", {
                "confidence": 0.99,
                "reason": (
                    "deterministic_recipient_fact_drill_down"
                    if fact_field is not None
                    else "deterministic_recipient_drill_down"
                ),
                "delta_type": "filter",
                "recipient_name": recipient_name,
                "fact_field": fact_field,
            }

        surface_mode = self._surface_mode(surface_view)
        if any(phrase in normalized for phrase in _RETRANSFER_PHRASES) and surface_mode in {
            SurfaceViewMode.DIRECT_ANSWER,
            SurfaceViewMode.TRANSACTION_LIST,
        }:
            return "drill_down", {
                "confidence": 0.98,
                "reason": "deterministic_retransfer",
                "drill_down_index": 0,
                "drill_down_action": "re_transfer",
            }

        time_match = _TIME_DELTA_RE.match(normalized)
        if time_match and surface_view is not None:
            return "time_delta", {
                "confidence": 0.95,
                "reason": "deterministic_time_delta",
                "time_period": time_match.group(1).lower(),
            }

        if normalized in _AGGREGATE_PATTERNS and surface_view is not None:
            return "aggregate", {
                "confidence": 0.95,
                "reason": "deterministic_aggregate",
            }

        filter_match = _FILTER_DELTA_RE.match(normalized)
        if filter_match and surface_view is not None:
            return "filter_delta", {
                "confidence": 0.95,
                "reason": "deterministic_filter_delta",
                "transaction_type": filter_match.group(1).lower(),
            }

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
