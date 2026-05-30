"""Structural guardrails for active query sessions."""

from __future__ import annotations

import re
from typing import Any

from banking.presentation.i18n.renderer import render_message

_END_SESSION_PATTERNS = (
    r"\bthanks?\b",
    r"\bthank you\b",
    r"\bi'?m done\b",
    r"\bdone\b",
    r"\be se\b",
    r"\bese\b",
)


class ContinuationClassifier:
    """Deterministic structural guardrails used by the query reasoner."""

    def _guardrail_classify(
        self,
        *,
        message: str,
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
        return None

    @staticmethod
    def _normalize_message(message: str) -> str:
        return " ".join(message.lower().strip().split())

    @staticmethod
    def _strip_trailing_punctuation(text: str) -> str:
        return re.sub(r"[?.!,]+$", "", text).strip()
