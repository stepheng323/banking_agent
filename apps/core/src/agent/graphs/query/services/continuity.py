"""Grounded continuity helpers for query session exits and surface actions."""

import re

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
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


class ContinuationClassifier:
    """Deterministic continuation helpers used by the query reasoner."""

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

    def _guardrail_classify(
        self,
        *,
        message: str,
        items: list[QueryResultItem] | None,
        surface: ResultSurface | None,
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

        recipient_name = self._resolve_beneficiary_summary_recipient_reply(message, items=items, surface=surface)
        if recipient_name:
            return "recipient_drill_down", {
                "confidence": 0.99,
                "reason": "deterministic_recipient_drill_down",
                "delta_type": "filter",
                "recipient_name": recipient_name,
            }

        if (
            any(phrase in normalized for phrase in _RETRANSFER_PHRASES)
            and surface
            and surface.type
            in {
                SurfaceType.SINGLE_ITEM,
                SurfaceType.LIST,
            }
        ):
            return "drill_down", {
                "confidence": 0.98,
                "reason": "deterministic_retransfer",
                "drill_down_index": 0,
                "drill_down_action": "re_transfer",
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
