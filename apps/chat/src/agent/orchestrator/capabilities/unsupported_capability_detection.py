"""Deterministic detection for unsupported capabilities."""

import re
import unicodedata
from collections.abc import Iterable

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import UnsupportedCapability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import UNSUPPORTED_CAPABILITY_REGISTRY

SEMANTIC_UNSUPPORTED_CANDIDATE_RE = re.compile(
    r"\b(?:"
    r"loan|borrow|lend|credit|advance|invest|investment|crypto|bitcoin|btc|ethereum|eth|stock|shares?|"
    r"forex|fx|advice|advise|recommend|abroad|international|dollar|usd|swift|iban|export|download|"
    r"pdf|csv|spreadsheet|all[-\s]?time|lifetime|entire\s+history|grow\s+(?:my\s+)?money|"
    r"wealth|returns?|profit|staking?|stake|portfolio|"
    r"owo|kudi|ego|jari|bashi|lamuni|rance|awin|gbese|mbinye|okeere|waje|ofesi"
    r")\b",
    re.IGNORECASE,
)


def normalize_unsupported_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_marks.strip().casefold()).strip()


def detect_unsupported_capability(text: str | None) -> UnsupportedCapability | None:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return None
    for capability in UNSUPPORTED_CAPABILITY_REGISTRY:
        if any(pattern.search(normalized) for pattern in capability.patterns):
            return capability
    return None


def detect_unsupported_capabilities(
    text: str | None,
    *,
    allowed_policy_labels: Iterable[str] | None = None,
) -> list[UnsupportedCapability]:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return []
    allowed = {label.casefold() for label in allowed_policy_labels or () if label}
    detected: list[UnsupportedCapability] = []
    for capability in UNSUPPORTED_CAPABILITY_REGISTRY:
        if allowed and capability.policy_label.casefold() not in allowed:
            continue
        if any(pattern.search(normalized) for pattern in capability.patterns):
            detected.append(capability)
    return detected


def should_try_semantic_unsupported_capability(text: str | None) -> bool:
    normalized = normalize_unsupported_text(text)
    if not normalized or len(normalized) > 240:
        return False
    return bool(SEMANTIC_UNSUPPORTED_CANDIDATE_RE.search(normalized))


def is_same_unsupported_capability_followup(text: str | None, capability: UnsupportedCapability) -> bool:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in capability.patterns):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in capability.followup_terms)
