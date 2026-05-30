import re
from dataclasses import dataclass
from typing import Literal

from apps.chat.src.agent.orchestrator.confirmation.confirmation_classifier import classify_confirmation_reply_sync
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS,
)

_BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS = 64
_BENEFICIARY_ALIAS_MARKERS = (" as ", " alias ", " name ", " called ", " oruko ", " suna ", " aha ", " nom ")
_BENEFICIARY_BARE_ALIAS_MAX_WORDS = 3
_BENEFICIARY_DISMISS_PHRASES = {
    "no",
    "no thanks",
    "not now",
    "later",
    "skip",
    "dont save",
    "don't save",
    "leave it",
    "ignore",
    "cancel",
}
_BENEFICIARY_SAVE_INTENT_RE = re.compile(
    r"\b("
    r"save|store|keep|remember|add|register|record|bookmark|"
    r"sauve|sauver|enregistre|enregistrer|garde|garder|"
    r"ajiye|adana|fipamo|pamo|toju|chekwa|debe"
    r")\b",
    re.IGNORECASE,
)
_BENEFICIARY_ALIAS_CAPTURE_PATTERNS = (
    re.compile(
        r"\b(?:save|store|keep|remember|add|register|record|bookmark|"
        r"sauve|sauver|enregistre|enregistrer|garde|garder|"
        r"ajiye|adana|fipamo|pamo|toju|chekwa|debe)\b"
        r"(?:[\w\s]{0,32})?"
        r"\b(?:as|alias|name|called|oruko|suna|aha|nom)\b[:\s\"'`-]*(?P<alias>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:save|store|keep|remember|add|register|record|bookmark|"
        r"sauve|sauver|enregistre|enregistrer|garde|garder|"
        r"ajiye|adana|fipamo|pamo|toju|chekwa|debe)\b[:\s\"'`-]+(?P<alias>.+)$",
        re.IGNORECASE,
    ),
)
_BENEFICIARY_QUOTED_ALIAS_RE = re.compile(r"[\"'](?P<alias>[^\"']{1,64})[\"']")
_BENEFICIARY_ALIAS_TRAILING_NOISE_RE = re.compile(
    r"\b("
    r"please|pls|abeg|thanks|thank you|thankyou|na|jare|biko|"
    r"don allah|jowo|s'il vous plait|sil vous plait|svp|stp"
    r")\b$",
    re.IGNORECASE,
)
_BENEFICIARY_ALIAS_ONLY_BLOCKLIST = {
    "it",
    "this",
    "that",
    "beneficiary",
    "recipient",
    "save",
    "yes",
    "okay",
    "ok",
    "sure",
}
_BENEFICIARY_BARE_ALIAS_BLOCKLIST = {
    "i",
    "im",
    "i'm",
    "me",
    "you",
    "your",
    "he",
    "she",
    "we",
    "they",
    "save",
    "call",
    "use",
    "name",
    "alias",
}
_BENEFICIARY_ALIAS_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'.-]*")
_SUGGESTION_TX_HINT_KEYWORDS = (
    "send",
    "transfer",
    "pay",
    "buy",
    "airtime",
    "data",
    "bundle",
    "fund",
    "withdraw",
    "balance",
    "statement",
    "transaction",
)
_SUGGESTION_TX_AMOUNT_PATTERN = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")
_SUGGESTION_TX_ACCOUNT_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_SUGGESTION_TX_BANK_NETWORK_PATTERN = re.compile(
    r"\b(bank|first bank|opay|palmpay|kuda|zenith|gtb|gtbank|uba|fidelity|access|"
    r"mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BeneficiarySuggestionDecision:
    action: Literal["save_default", "save_alias", "dismiss"]
    alias: str | None = None
    reason: str = "unknown"


def _normalize_suggestion_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _is_transaction_like_message(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in _SUGGESTION_TX_HINT_KEYWORDS):
        return True
    if _SUGGESTION_TX_AMOUNT_PATTERN.search(normalized_text):
        return True
    if _SUGGESTION_TX_ACCOUNT_PATTERN.search(normalized_text):
        return True
    return bool(_SUGGESTION_TX_BANK_NETWORK_PATTERN.search(normalized_text))


def _cleanup_alias(value: str) -> str | None:
    alias = value.strip().strip(".,;:!?- ")
    if not alias:
        return None

    alias = re.sub(r"^(?:is|na|be|named|called)\s+", "", alias, flags=re.IGNORECASE).strip()
    while True:
        cleaned = _BENEFICIARY_ALIAS_TRAILING_NOISE_RE.sub("", alias).strip(" .,;:!?-")
        if cleaned == alias:
            break
        alias = cleaned
    if not alias:
        return None
    if len(alias) > _BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS:
        alias = alias[:_BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS].rstrip()
    alias_token = alias.lower()
    if alias_token in _BENEFICIARY_ALIAS_ONLY_BLOCKLIST:
        return None
    return alias


def _extract_alias_from_text(message_text: str) -> str | None:
    quoted_match = _BENEFICIARY_QUOTED_ALIAS_RE.search(message_text)
    if quoted_match:
        alias = _cleanup_alias(quoted_match.group("alias"))
        if alias:
            return alias

    for pattern in _BENEFICIARY_ALIAS_CAPTURE_PATTERNS:
        match = pattern.search(message_text)
        if not match:
            continue
        alias = _cleanup_alias(match.group("alias"))
        if alias:
            return alias

    normalized = _normalize_suggestion_text(message_text)
    for marker in _BENEFICIARY_ALIAS_MARKERS:
        if marker not in normalized:
            continue
        prefix, suffix = normalized.rsplit(marker, 1)
        if not suffix or not prefix:
            continue
        if not _BENEFICIARY_SAVE_INTENT_RE.search(prefix):
            continue
        marker_idx = prefix.rfind(marker.strip())
        if marker_idx == -1:
            continue
        candidate = message_text[-len(suffix) :]
        alias = _cleanup_alias(candidate)
        if alias:
            return alias

    return None


def _is_bare_alias_candidate(message_text: str, alias: str) -> bool:
    normalized = _normalize_suggestion_text(message_text)
    if not normalized:
        return False
    if any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS):
        return False
    words = alias.split()
    if not words or len(words) > _BENEFICIARY_BARE_ALIAS_MAX_WORDS:
        return False
    lowered_words = {word.lower() for word in words}
    if lowered_words & _BENEFICIARY_BARE_ALIAS_BLOCKLIST:
        return False
    return all(_BENEFICIARY_ALIAS_TOKEN_RE.fullmatch(word) for word in words)


def _resolve_beneficiary_suggestion_reply(
    message_text: str,
    *,
    locale: str,
) -> BeneficiarySuggestionDecision:
    normalized = _normalize_suggestion_text(message_text)
    if not normalized:
        return BeneficiarySuggestionDecision(action="dismiss", reason="empty_message")

    if _is_transaction_like_message(normalized):
        return BeneficiarySuggestionDecision(action="dismiss", reason="transaction_guard")

    confirmation_decision = classify_confirmation_reply_sync(
        message_text,
        prompt_kind="beneficiary_save",
        locale=locale,
    )
    if confirmation_decision.action == "reject" or normalized in _BENEFICIARY_DISMISS_PHRASES:
        return BeneficiarySuggestionDecision(action="dismiss", reason="explicit_dismiss")

    has_save_intent = bool(_BENEFICIARY_SAVE_INTENT_RE.search(normalized))
    extracted_alias = _extract_alias_from_text(message_text)
    if has_save_intent and extracted_alias:
        return BeneficiarySuggestionDecision(action="save_alias", alias=extracted_alias, reason="explicit_save_alias")
    if has_save_intent:
        return BeneficiarySuggestionDecision(action="save_default", reason="explicit_save")

    if confirmation_decision.action == "approve":
        return BeneficiarySuggestionDecision(action="save_default", reason="pure_affirmation")

    bare_alias = _cleanup_alias(message_text)
    if bare_alias and _is_bare_alias_candidate(message_text, bare_alias):
        return BeneficiarySuggestionDecision(action="save_alias", alias=bare_alias, reason="bare_alias_reply")

    return BeneficiarySuggestionDecision(action="dismiss", reason="ambiguous_dismiss")
