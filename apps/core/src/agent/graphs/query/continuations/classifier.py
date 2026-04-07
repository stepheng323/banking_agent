"""Deterministic continuation classification for active query sessions."""

from __future__ import annotations

import re
from typing import Any

from apps.core.src.agent.graphs.query.models import QueryExecutionContract, QueryIntent, QueryResultItem
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
_RECIPIENT_DELTA_RE = re.compile(r"^(?:what about|how about|and)\s+(.+?)(?:\?|!|\.)?$", re.IGNORECASE)
_FRESH_LIST_RESET_PATTERNS = (
    re.compile(
        r"^(?:show|list|check|display|see|get|view)\s+.+\brecent\b.+\b(?:transactions?|debits?|credits?|payments?)\b(?:.*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show|list|check|display|see|get|view)\s+all\s+my\s+(?:transactions?|debits?|credits?|payments?)(?:\?|!|\.)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show|list|check|display|see|get|view)\s+(?:all|latest|recent)\s+(?:transactions?|debits?|credits?|payments?)(?:\?|!|\.)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show|list|check|display|see|get|view)\s+.+\b(?:today|today's|yesterday|yesterday's|"
        r"this week|this week's|last week|last week's|this month|this month's|last month|last month's|"
        r"this year|this year's|last year|last year's)\b.+\b(?:transactions?|transaction|debits?|credits?|payments?)\b(?:.*)?$",
        re.IGNORECASE,
    ),
)
_DAY_SCOPED_SINGULAR_LIST_RE = re.compile(
    r"^(?:show|list|check|display|see|get|view)\s+"
    r"(?:(?:my|all my|all)\s+)?"
    r"(?:today(?:'s)?|yesterday(?:'s)?|this week(?:'s)?|last week(?:'s)?|"
    r"this month(?:'s)?|last month(?:'s)?|this year(?:'s)?|last year(?:'s)?)\s+"
    r"(?:transactions?|transaction|debits?|credits?|payments?)(?:\?|!|\.)?$",
    re.IGNORECASE,
)
_BENEFICIARY_FACT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwhen\b|\bwhat date\b", "date"),
    (r"\bwhich bank\b|\bwhat bank\b", "bank"),
    (r"\bhow much\b|\bwhat(?:'s| is)? the amount\b|\bamount\b", "amount"),
)


class ContinuationClassifier:
    """Deterministic continuation helpers used by the query reasoner."""

    def _guardrail_classify(
        self,
        *,
        message: str,
        items: list[QueryResultItem] | None,
        surface_view: SurfaceView | None,
        language: str,
        query_contract: QueryExecutionContract | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        return self.guardrail_classify(
            message=message,
            items=items,
            surface_view=surface_view,
            language=language,
            query_contract=query_contract,
        )

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

    def _is_fresh_list_reset_request(self, message: str) -> bool:
        normalized = self._normalize_message(message)
        if _DAY_SCOPED_SINGULAR_LIST_RE.match(normalized):
            return True
        return any(pattern.match(normalized) for pattern in _FRESH_LIST_RESET_PATTERNS)

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

    def _resolve_recipient_delta_reply(
        self,
        *,
        message: str,
        surface_view: SurfaceView | None,
        query_contract: QueryExecutionContract | None,
    ) -> str | None:
        if query_contract is None or query_contract.filters is None or not query_contract.filters.counterparty:
            return None
        if query_contract.intent not in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH}:
            return None
        surface_mode = self._surface_mode(surface_view)
        if surface_mode not in {SurfaceViewMode.DIRECT_ANSWER, SurfaceViewMode.TRANSACTION_LIST}:
            return None
        candidate_match = _RECIPIENT_DELTA_RE.match(self._normalize_message(message))
        if candidate_match is None:
            return None
        candidate = self._strip_trailing_punctuation(candidate_match.group(1))
        if not candidate:
            return None
        lowered = candidate.lower()
        if lowered in {
            "today",
            "yesterday",
            "last week",
            "this week",
            "last month",
            "this month",
            "credit",
            "credits",
            "debit",
            "debits",
        }:
            return None
        if any(ch.isdigit() for ch in candidate):
            return None
        return " ".join(candidate.split())

    def guardrail_classify(
        self,
        *,
        message: str,
        items: list[QueryResultItem] | None,
        surface_view: SurfaceView | None,
        language: str,
        query_contract: QueryExecutionContract | None = None,
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

        scoped_recipient = self._resolve_recipient_delta_reply(
            message=message,
            surface_view=surface_view,
            query_contract=query_contract,
        )
        if scoped_recipient:
            return "recipient_drill_down", {
                "confidence": 0.95,
                "reason": "deterministic_scoped_recipient_delta",
                "delta_type": "filter",
                "recipient_name": scoped_recipient,
                "fact_field": None,
            }

        if surface_view is not None and self._is_fresh_list_reset_request(message):
            return "fresh_query_reset", {
                "confidence": 0.97,
                "reason": "deterministic_fresh_list_reset",
            }

        return None
