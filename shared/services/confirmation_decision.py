"""Bounded confirmation/rejection classification for prompt-scoped replies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.i18n import LocaleManager
from shared.i18n.models import LocaleCode
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ConfirmationAction = Literal["approve", "reject", "modify", "new_request", "unclear"]
ConfirmationDecisionSource = Literal["fastpath", "llm", "guardrail"]
ConfirmationPromptKind = Literal[
    "transaction_confirmation",
    "resume_prompt",
    "beneficiary_save",
    "amount_suggestion",
]

APPROVAL_CONFIDENCE_THRESHOLD = 0.90
REJECTION_CONFIDENCE_THRESHOLD = 0.85

SUPPORTED_CONFIRMATION_LOCALES = {
    LocaleCode.EN,
    LocaleCode.PCM,
    LocaleCode.YO,
    LocaleCode.HA,
    LocaleCode.IG,
}

APPROVE_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "yep please",
        "ok",
        "okay",
        "sure",
        "sure please",
        "proceed",
        "go ahead",
        "confirm",
    },
    LocaleCode.PCM: {"yes na", "abeg proceed", "ok na"},
    LocaleCode.YO: {"beeni", "o dara", "siwaju"},
    LocaleCode.HA: {"na'am", "naam", "eh"},
    LocaleCode.IG: {"ee", "kwe", "ga n'ihu", "gaa n'ihu"},
}

REJECT_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {
        "no",
        "no thanks",
        "no thank you",
        "not now",
        "later",
        "do not proceed",
        "don't proceed",
    },
    LocaleCode.PCM: {"no o", "no abeg", "not now", "later"},
    LocaleCode.YO: {"rara", "ma se", "dawoduro"},
    LocaleCode.HA: {"a'a", "a a", "dakatar", "ba yanzu ba"},
    LocaleCode.IG: {"mba", "kwusi", "ugbua a"},
}

CANCEL_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"cancel", "abort", "stop", "nevermind", "never mind"},
    LocaleCode.PCM: {"cancel", "abort", "stop", "commot", "no do again"},
    LocaleCode.YO: {"fagile", "da duro", "ma se", "dawoduro"},
    LocaleCode.HA: {"soke", "dakatar"},
    LocaleCode.IG: {"kagbuo", "kwusi"},
}

_PROMPT_APPROVE_EXTRAS: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]] = {
    "resume_prompt": {
        LocaleCode.EN: {
            "continue",
            "continue it",
            "continue please",
            "resume",
            "resume it",
            "resume please",
            "please continue",
            "please resume",
            "go back",
            "that transfer",
            "continue the transfer",
        },
        LocaleCode.PCM: {"abeg continue", "continue am", "resume am"},
        LocaleCode.YO: {"tesiwaju", "tesiwaju e", "tesiwaju jowo"},
        LocaleCode.HA: {"ci gaba", "ci gaba da shi"},
        LocaleCode.IG: {"bido ya", "continue ya"},
    },
    "beneficiary_save": {
        LocaleCode.EN: {"save", "save it", "save this", "save beneficiary"},
        LocaleCode.PCM: {"save am"},
        LocaleCode.YO: {"fipamo", "pamo", "toju"},
        LocaleCode.HA: {"ajiye", "adana"},
        LocaleCode.IG: {"chekwa", "debe"},
    },
    "transaction_confirmation": {},
    "amount_suggestion": {},
}

_PROMPT_REJECT_EXTRAS: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]] = {
    "resume_prompt": {
        LocaleCode.EN: {"leave it", "leave it please", "dismiss", "cancel that", "forget it"},
        LocaleCode.PCM: {"leave am", "forget am", "cancel am"},
        LocaleCode.YO: {"fagile"},
        LocaleCode.HA: {"soke"},
        LocaleCode.IG: {"kagbuo"},
    },
    "beneficiary_save": {
        LocaleCode.EN: {"skip", "dont save", "don't save", "leave it", "ignore"},
        LocaleCode.PCM: {"no save am"},
    },
    "transaction_confirmation": {},
    "amount_suggestion": {},
}

_TRIM_CHARS = '.,!?;:"`~()[]{}'
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


class ConfirmationDecisionOutput(BaseModel):
    """Structured LLM output for prompt-scoped confirmation decisions."""

    action: ConfirmationAction = Field(
        description="Classify only the reply to the active prompt: approve, reject, modify, new_request, or unclear."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="llm_classification")
    custom_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConfirmationDecision:
    action: ConfirmationAction
    source: ConfirmationDecisionSource
    confidence: float
    reason: str
    custom_data: dict[str, Any] | None = field(default=None)

    @property
    def is_approval(self) -> bool:
        return self.action == "approve"

    @property
    def is_rejection(self) -> bool:
        return self.action == "reject"

    @property
    def is_unclear(self) -> bool:
        return self.action == "unclear"


def normalize_confirmation_text(text: str | None) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return compact.strip(_TRIM_CHARS)


def normalize_confirmation_locale(value: str | LocaleCode | None) -> LocaleCode | None:
    if isinstance(value, LocaleCode):
        return value if value in SUPPORTED_CONFIRMATION_LOCALES else None
    if not value:
        return None
    normalized = LocaleManager.normalize(str(value).strip().lower())
    return normalized if normalized in SUPPORTED_CONFIRMATION_LOCALES else None


def confirmation_approve_phrases(prompt_kind: ConfirmationPromptKind) -> dict[LocaleCode, set[str]]:
    return _phrases_for_kind(APPROVE_PHRASES_BY_LOCALE, _PROMPT_APPROVE_EXTRAS, prompt_kind)


def confirmation_reject_phrases(prompt_kind: ConfirmationPromptKind) -> dict[LocaleCode, set[str]]:
    return _phrases_for_kind(REJECT_PHRASES_BY_LOCALE, _PROMPT_REJECT_EXTRAS, prompt_kind)


def _phrases_for_kind(
    base: dict[LocaleCode, set[str]],
    extras: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]],
    prompt_kind: ConfirmationPromptKind,
) -> dict[LocaleCode, set[str]]:
    result = {locale: set(phrases) for locale, phrases in base.items()}
    for locale, phrases in extras.get(prompt_kind, {}).items():
        result.setdefault(locale, set()).update(phrases)
    return result


def _locale_candidates(locale: LocaleCode | None) -> list[LocaleCode]:
    ordered: list[LocaleCode] = []
    if locale in SUPPORTED_CONFIRMATION_LOCALES:
        ordered.append(locale)
    for fallback in (LocaleCode.EN, LocaleCode.PCM, LocaleCode.YO, LocaleCode.HA, LocaleCode.IG):
        if fallback not in ordered:
            ordered.append(fallback)
    return ordered


def _guardrail_decision(normalized: str, prompt_kind: ConfirmationPromptKind) -> ConfirmationDecision | None:
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


def classify_confirmation_reply_sync(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
    locale: str | LocaleCode | None = None,
) -> ConfirmationDecision:
    """Fast, deterministic confirmation classification with no fuzzy matching."""
    normalized = normalize_confirmation_text(text)
    guardrail = _guardrail_decision(normalized, prompt_kind)
    if guardrail is not None:
        return guardrail

    resolved_locale = normalize_confirmation_locale(locale)
    approve_phrases = confirmation_approve_phrases(prompt_kind)
    reject_phrases = confirmation_reject_phrases(prompt_kind)
    for candidate_locale in _locale_candidates(resolved_locale):
        if normalized in approve_phrases.get(candidate_locale, set()):
            return ConfirmationDecision("approve", "fastpath", 0.99, "phrase_approve")
        reject_set = set(reject_phrases.get(candidate_locale, set()))
        reject_set.update(CANCEL_PHRASES_BY_LOCALE.get(candidate_locale, set()))
        if normalized in reject_set:
            return ConfirmationDecision("reject", "fastpath", 0.99, "phrase_reject")

    return ConfirmationDecision("unclear", "fastpath", 0.0, "no_match")


def is_safe_guarded_approval_text(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
) -> bool:
    normalized = normalize_confirmation_text(text)
    guardrail = _guardrail_decision(normalized, prompt_kind)
    return guardrail is None


def confirmation_decision_messages(
    *,
    text: str,
    prompt_kind: ConfirmationPromptKind,
    context: str,
) -> list[dict[str, str]]:
    system_prompt = (
        "Classify the user's reply to one active banking prompt. "
        "Return approve only when the user clearly agrees to the active prompt. "
        "Return reject when they decline, cancel, or defer. "
        "Return modify when they change amount, recipient, bank, account, source, or other details. "
        "Return new_request when they ask for a fresh banking task like a balance, transfer, airtime/data, "
        "receipt, beneficiary list, or transaction query. Return unclear when uncertain. "
        "Do not invent transaction facts."
    )
    user_prompt = (
        f"Prompt kind: {prompt_kind}\n"
        f"Context: {context or 'None'}\n"
        f"User reply: \"\"\"{text}\"\"\""
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_confirmation_reply(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
    locale: str | LocaleCode | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> ConfirmationDecision:
    """Classify a prompt reply with fastpath first and guarded LLM fallback."""
    fastpath = classify_confirmation_reply_sync(text, prompt_kind=prompt_kind, locale=locale)
    if fastpath.action != "unclear" or fastpath.reason != "no_match":
        return fastpath
    if structured_llm is None:
        return fastpath

    try:
        output = await structured_llm.ainvoke(
            confirmation_decision_messages(text=text, prompt_kind=prompt_kind, context=context)
        )
        parsed = (
            output
            if isinstance(output, ConfirmationDecisionOutput)
            else ConfirmationDecisionOutput.model_validate(output)
        )
    except Exception as exc:
        logger.warning("confirmation_decision_llm_failed", error=str(exc), prompt_kind=prompt_kind)
        return fastpath

    if parsed.action == "approve":
        if parsed.confidence < APPROVAL_CONFIDENCE_THRESHOLD:
            return ConfirmationDecision("unclear", "llm", parsed.confidence, "approval_below_threshold")
        if not is_safe_guarded_approval_text(text, prompt_kind=prompt_kind):
            return ConfirmationDecision("unclear", "guardrail", parsed.confidence, "unsafe_llm_approval")
        return ConfirmationDecision("approve", "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    if parsed.action == "reject":
        if parsed.confidence < REJECTION_CONFIDENCE_THRESHOLD:
            return ConfirmationDecision("unclear", "llm", parsed.confidence, "rejection_below_threshold")
        return ConfirmationDecision("reject", "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    if parsed.action in {"modify", "new_request"}:
        return ConfirmationDecision(parsed.action, "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    return ConfirmationDecision("unclear", "llm", parsed.confidence, parsed.reason, parsed.custom_data)
