"""Guardrails for prompt-scoped confirmation classification."""

from __future__ import annotations

import re

from banking.transactions.shared.confirmation.models import (
    ConfirmationDecision,
    ConfirmationPromptKind,
)
from banking.transactions.shared.confirmation.phrases import normalize_confirmation_text

_ACCOUNT_NUMBER_RE = re.compile(r"\b\d{10,11}\b")
_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b", re.IGNORECASE)
_MODIFICATION_RE = re.compile(
    r"\b(?:change|update|edit|instead|make\s+it|add|use|using|from|amount|bank|account|recipient|"
    r"beneficiary|narration|memo|note|description|reason|purpose)\b",
    re.IGNORECASE,
)
_NEW_REQUEST_RE = re.compile(
    r"\b(?:send|transfer|pay|buy|airtime|data|bundle|recharge|top\s*up|balance|"
    r"show|list|check|beneficiar|recipient|receipt|transaction)\b",
    re.IGNORECASE,
)
_NEW_REQUEST_AMOUNT_RE = re.compile(
    r"\b(?:send|transfer|pay|buy|airtime|data|bundle|recharge|top\s*up)\b.*"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b",
    re.IGNORECASE,
)
_BALANCE_OR_QUERY_RE = re.compile(
    r"\b(?:balance|account\s+balance|transactions?|receipt|beneficiar(?:y|ies)|recipients?)\b",
    re.IGNORECASE,
)


def confirmation_guardrail_decision(
    normalized: str,
    prompt_kind: ConfirmationPromptKind,
) -> ConfirmationDecision | None:
    if not normalized:
        return ConfirmationDecision("unclear", "guardrail", 0.0, "empty_message")
    if len(normalized) > 160:
        return ConfirmationDecision("unclear", "guardrail", 0.0, "message_too_long")

    has_account = bool(_ACCOUNT_NUMBER_RE.search(normalized))
    has_amount = bool(_AMOUNT_RE.search(normalized))
    has_modify = bool(_MODIFICATION_RE.search(normalized))

    if has_account or has_modify:
        return ConfirmationDecision("modify", "guardrail", 0.99, "modification_or_account_detail")

    if prompt_kind == "resume_prompt":
        if _NEW_REQUEST_AMOUNT_RE.search(normalized) or _BALANCE_OR_QUERY_RE.search(normalized):
            return ConfirmationDecision("new_request", "guardrail", 0.99, "fresh_banking_request")

    if has_amount and _NEW_REQUEST_RE.search(normalized):
        return ConfirmationDecision("new_request", "guardrail", 0.99, "fresh_banking_request")
    if has_amount:
        return ConfirmationDecision("modify", "guardrail", 0.99, "amount_detail")

    return None


def is_safe_guarded_approval_text(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
) -> bool:
    normalized = normalize_confirmation_text(text)
    guardrail = confirmation_guardrail_decision(normalized, prompt_kind)
    return guardrail is None


__all__ = ["confirmation_guardrail_decision", "is_safe_guarded_approval_text"]
