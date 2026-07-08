"""Transaction intent fast-path classifiers for the gate workflow."""

import re

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.bank_details import (
    _has_recipient_bank_details_shape,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.language import _allow_phrase_heavy_fastpath
from banking.presentation.i18n.locale import LocaleManager

SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")
_POLITE_PREFIX = (
    r"(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly|"
    r"oh|very\s+good|good|great|nice|cool|alright|thanks|thank\s+you)\s+)*"
)
_TRANSFER_VERB_PATTERN = r"(?:send|transfer|pay|remit)"
_SOURCE_CUE_PATTERN = r"(?:use|using|from|with|debit|via)"
_TRANSFER_DIRECT_PREFIX_RE = re.compile(
    rf"^{_POLITE_PREFIX}"
    r"(?:send|transfer|pay|remit|split|fi|tura)\b",
    re.IGNORECASE,
)
_TRANSFER_SOURCE_FIRST_PREFIX_RE = re.compile(
    rf"^{_POLITE_PREFIX}"
    r"(?:use|using|from|with)\s+(?:my\s+)?[a-z][\w\s]{0,32}?\s+"
    r"(?:to\s+)?(?:send|transfer|pay|remit)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_RECIPIENT_CUE_RE = re.compile(r"\b(?:to|for|between|btw|si|zuwa)\b", re.IGNORECASE)
_TRANSFER_DIRECT_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")
_NIGERIAN_PHONE_RE_FRAGMENT = r"(?<!\d)(?:\+?234[\s().-]*[789]|0[789])(?:[\s().-]*\d){9}(?!\d)"
_PHONE_NUMBER_CUE_RE = re.compile(_NIGERIAN_PHONE_RE_FRAGMENT, re.IGNORECASE)
_TRANSFER_DIRECT_PERCENTAGE_RE = re.compile(
    r"\b(?:half|quarter|tithe|\d{1,3}\s*%|all|everything|max amount|what(?:ever)? i have)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_SOURCE_RE = re.compile(
    r"\b(?:from|using|use|with)\s+(?:my\s+)?[a-z][\w\s]{0,24}\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_NON_TRANSFER_RE = re.compile(
    r"\b(?:transaction|transactions|history|statement|income|inflow|expense|expenses|spending|"
    r"balance|linked accounts?|beneficiar(?:y|ies)|save beneficiary|support|reversal|receipt|"
    r"airtime|data|bundle|show|list|view|get)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_QUERY_MARKER_RE = re.compile(
    r"\b(?:how much|what(?:'s| is)?|when|who did i|show|list|view|get)\b",
    re.IGNORECASE,
)
_TRANSFER_MULTI_RECIPIENT_TAIL_RE = re.compile(
    r"\b(?:to|for|between|btw)\b\s+.+\b(?:and|&)\b\s+.+",
    re.IGNORECASE,
)
_MEDIA_CAPTION_INSTRUCTION_RE = re.compile(
    r"^\s*User caption/instruction:\s*(?P<caption>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_AIRTIME_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:(?:i\s+)?(?:want|need|would\s+like)\s+to\s+)?"
    r"(?:buy|recharge|top\s*up|topup|load|send)\b",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_HINT_RE = re.compile(
    rf"\b(?:airtime|mtn|glo|airtel|9mobile)\b|{_NIGERIAN_PHONE_RE_FRAGMENT}",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_EXPLICIT_HINT_RE = re.compile(
    r"\b(?:airtime|mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_EXPLICIT_AIRTIME_RE = re.compile(r"\bairtime\b", re.IGNORECASE)
_AIRTIME_DIRECT_SEND_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*send\b",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_DATA_WORD_RE = re.compile(r"\b(?:data|bundle)\b", re.IGNORECASE)
_DATA_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:(?:i\s+)?(?:want|need|would\s+like)\s+to\s+)?"
    r"(?:buy|get|send)\b",
    re.IGNORECASE,
)
_DATA_DIRECT_HINT_RE = re.compile(
    r"\b(?:data|bundle)\b|\d+\s*(?:mb|gb)\b",
    re.IGNORECASE,
)
_DATA_DIRECT_SIZE_RE = re.compile(r"\d+\s*(?:mb|gb)\b", re.IGNORECASE)
_DATA_DIRECT_NON_PURCHASE_CONTEXT_RE = re.compile(
    r"\b(?:transaction|transactions|history|statement|records?|details?|account|accounts|balance|"
    r"status|failed|failure|pending|receipt|proof|refund|reversal|ticket|complaint|support)\b",
    re.IGNORECASE,
)
_MIXED_TRANSFER_CLAUSE_RE = re.compile(
    r"\b(?:send|transfer|pay|remit|split)\b.*(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?",
    re.IGNORECASE,
)
_MIXED_AIRTIME_CLAUSE_RE = re.compile(
    r"\b(?:buy|recharge|top\s*up|topup|load)\b.*\b(?:airtime|mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)
_MIXED_DATA_CLAUSE_RE = re.compile(
    r"\b(?:buy|get|send)\b.*\b(?:data|bundle|\d+\s*(?:mb|gb))\b",
    re.IGNORECASE,
)



def _is_obvious_airtime_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized or not _AIRTIME_DIRECT_PREFIX_RE.search(normalized):
        return False
    if _AIRTIME_DIRECT_DATA_WORD_RE.search(normalized):
        return False
    if _AIRTIME_DIRECT_SEND_PREFIX_RE.match(normalized) and not _AIRTIME_DIRECT_EXPLICIT_HINT_RE.search(normalized):
        return False
    has_mixed_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_mixed_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    if _AIRTIME_DIRECT_EXPLICIT_AIRTIME_RE.search(normalized):
        return True
    return bool(_TRANSFER_DIRECT_AMOUNT_RE.search(normalized) and _AIRTIME_DIRECT_HINT_RE.search(normalized))


def _is_obvious_data_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized or not _DATA_DIRECT_PREFIX_RE.search(normalized):
        return False
    has_mixed_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_mixed_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    if _DATA_DIRECT_NON_PURCHASE_CONTEXT_RE.search(normalized) and not _DATA_DIRECT_SIZE_RE.search(normalized):
        return False
    return bool(_DATA_DIRECT_HINT_RE.search(normalized))


def _clean_source_text(value: str | None) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" \t\r\n.,;:!?\"'()[]{}")
    text = re.sub(r"^(?:my|the)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+account$", "", text, flags=re.IGNORECASE).strip()
    return text


def _clean_slot_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n.,;:!?\"'()[]{}")




def _looks_like_multi_recipient_transfer(normalized: str) -> bool:
    if "split" in normalized or " each " in f" {normalized} " or re.search(r"\b(?:between|btw)\b", normalized):
        return True
    amount_matches = [
        match for match in _TRANSFER_DIRECT_AMOUNT_RE.findall(normalized) if not re.fullmatch(r"\s*\d{10,11}\s*", match)
    ]
    if len(amount_matches) >= 2:
        return True
    if not _TRANSFER_MULTI_RECIPIENT_TAIL_RE.search(normalized):
        return False
    if _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    return True


def _media_caption_instruction(message_text: str) -> str | None:
    match = _MEDIA_CAPTION_INSTRUCTION_RE.search(message_text or "")
    if not match:
        return None
    caption = re.sub(r"\s+", " ", match.group("caption")).strip()
    return caption or None


def _classify_obvious_transfer_request(message_text: str) -> str | None:
    caption_instruction = _media_caption_instruction(message_text)
    classification_text = caption_instruction or message_text
    normalized = re.sub(r"\s+", " ", classification_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return None
    if _has_recipient_bank_details_shape(classification_text):
        return "recipient_bank_details_only"
    has_transfer_prefix = bool(_TRANSFER_DIRECT_PREFIX_RE.search(normalized))
    has_source_first_prefix = bool(_TRANSFER_SOURCE_FIRST_PREFIX_RE.search(normalized))
    if not has_transfer_prefix and not has_source_first_prefix:
        return None
    if _TRANSFER_DIRECT_QUERY_MARKER_RE.search(normalized) and not has_transfer_prefix:
        return None
    has_multi_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_multi_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return None
    if _is_account_balance_request(normalized) or _is_query_domain_request(normalized):
        return None
    if _PHONE_NUMBER_CUE_RE.search(normalized):
        return None
    if _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized) and not _TRANSFER_DIRECT_RECIPIENT_CUE_RE.search(normalized):
        return None

    if _looks_like_multi_recipient_transfer(normalized):
        return "batch_transfer_command"
    if _TRANSFER_DIRECT_PERCENTAGE_RE.search(normalized) or _TRANSFER_DIRECT_SOURCE_RE.search(normalized):
        return "account_aware_transfer_command"

    has_amount_like = bool(_TRANSFER_DIRECT_AMOUNT_RE.search(normalized))
    has_transfer_shape = bool(_TRANSFER_DIRECT_RECIPIENT_CUE_RE.search(normalized))
    if not has_amount_like:
        return None
    if not has_transfer_shape:
        return "fresh_transfer_missing_recipient_command"

    return "fresh_transfer_command"


def _obvious_mixed_transaction_executors(message_text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return []

    executors: list[str] = []
    has_data_clause = bool(_MIXED_DATA_CLAUSE_RE.search(normalized))
    has_airtime_clause = bool(_MIXED_AIRTIME_CLAUSE_RE.search(normalized))
    if has_data_clause and not _AIRTIME_DIRECT_EXPLICIT_AIRTIME_RE.search(normalized):
        has_airtime_clause = False

    if _MIXED_TRANSFER_CLAUSE_RE.search(normalized):
        executors.append("transfer")
    if has_airtime_clause:
        executors.append("airtime")
    if has_data_clause:
        executors.append("data")
    if len(executors) < 2:
        return []
    return executors


def classify_obvious_transfer_request(message_text: str, *, locale: str | None = None) -> str | None:
    """Public helper for pre-graph fast-path hints."""
    if locale and not _allow_phrase_heavy_fastpath(message_text, LocaleManager.normalize(locale).value):
        return None
    return _classify_obvious_transfer_request(message_text)


__all__ = [
    "SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS",
    "_classify_obvious_transfer_request",
    "_is_obvious_airtime_request",
    "_is_obvious_data_request",
    "_obvious_mixed_transaction_executors",
    "classify_obvious_transfer_request",
]
