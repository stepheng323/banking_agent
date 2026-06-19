"""Transaction intent fast-path classifiers for the gate workflow."""

import re
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.bank_details import (
    _has_recipient_bank_details_shape,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.language import _allow_phrase_heavy_fastpath
from banking.presentation.i18n.locale import LocaleManager
from banking.transactions.shared.source_account_guard import find_account_by_bank_name
from banking.transfers.extraction.parsers import parse_amount_input
from shared.utils.bank_aliases import display_bank_name

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
_SOURCE_AWARE_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b", re.IGNORECASE)
_SOURCE_AWARE_SOURCE_FIRST_RE = re.compile(
    rf"^{_POLITE_PREFIX}"
    rf"(?P<cue>{_SOURCE_CUE_PATTERN})\s+(?:my\s+)?"
    rf"(?P<source>[a-z0-9&.' -]{{2,40}}?)"
    rf"(?:\s+account)?(?:\s*,\s*|\s+and\s+|\s+)"
    rf"(?:to\s+)?(?P<verb>{_TRANSFER_VERB_PATTERN})\s+"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_AWARE_VERB_AMOUNT_FROM_SOURCE_RE = re.compile(
    rf"^{_POLITE_PREFIX}"
    rf"(?P<verb>{_TRANSFER_VERB_PATTERN})\s+"
    rf"(?P<amount>{_SOURCE_AWARE_AMOUNT_RE.pattern})\s+"
    r"from\s+(?:my\s+)?(?P<source>[a-z0-9&.' -]{2,40}?)(?:\s+account)?\s+"
    r"to\s+(?P<target>.+)$",
    re.IGNORECASE,
)
_SOURCE_AWARE_VERB_AMOUNT_TO_TARGET_FROM_SOURCE_RE = re.compile(
    rf"^{_POLITE_PREFIX}"
    rf"(?P<verb>{_TRANSFER_VERB_PATTERN})\s+"
    rf"(?P<amount>{_SOURCE_AWARE_AMOUNT_RE.pattern})\s+"
    r"to\s+(?P<target>.+?)\s+"
    rf"(?P<cue>{_SOURCE_CUE_PATTERN})\s+(?:my\s+)?"
    r"(?P<source>[a-z0-9&.' -]{2,40}?)(?:\s+account)?$",
    re.IGNORECASE,
)
_SOURCE_AWARE_NARRATION_LABEL_RE = re.compile(
    r"\s+(?:narration|remark|memo|note|description)"
    r"(?:\s+(?:is|as|should\s+be))?\s*[:\-]?\s+(?P<narration>.+)$",
    re.IGNORECASE,
)
_SOURCE_AWARE_RECIPIENT_MULTI_RE = re.compile(r"(?:\s+(?:and|&)\s+|,)", re.IGNORECASE)
_SOURCE_AWARE_RECIPIENT_BLOCK_RE = re.compile(
    r"\b(?:airtime|data|bundle|balance|transaction|transactions|statement|show|list|check|"
    r"beneficiar(?:y|ies)|account(?:s)?|support|help|faq|cancel|stop)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceAwareTransferDirectParse:
    amount: float
    recipient_name: str
    source_bank_name: str
    narration: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "send_money",
            "amount": self.amount,
            "recipient_name": self.recipient_name,
            "source_bank_name": self.source_bank_name,
            "skip_extraction": True,
            "transfer_all": False,
            "is_self": False,
        }
        if self.narration:
            payload["narration"] = self.narration
        return payload


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


def _clean_narration(value: str | None) -> str | None:
    narration = _clean_slot_text(value)
    if not narration or _SOURCE_AWARE_RECIPIENT_BLOCK_RE.search(narration):
        return None
    if narration.islower():
        return narration[:1].upper() + narration[1:]
    return narration


def _unique_account_entries(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for account in accounts:
        bank_name = str(account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or "")
        account_number = str(account.get("account_number") or account.get("account") or "")
        account_id = str(account.get("id") or account.get("account_id") or "")
        key = (account_id, account_number, bank_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(account)
    return unique


def _match_unique_source_bank(accounts: list[dict[str, Any]], source_text: str) -> str | None:
    source = _clean_source_text(source_text)
    if not source:
        return None

    unique_accounts = _unique_account_entries(accounts)
    matches = [account for account in unique_accounts if find_account_by_bank_name([account], source) is not None]
    if len(matches) != 1:
        return None

    bank_name = str(
        matches[0].get("bank_name") or matches[0].get("bank") or matches[0].get("source_bank_name") or source
    ).strip()
    return display_bank_name(bank_name) or bank_name or None


def _parse_source_aware_amount_token(text: str) -> float | None:
    amount = parse_amount_input(text.strip())
    if amount is None or amount < 1000:
        return None
    return float(amount)


def _source_aware_amount_matches(body: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for match in _SOURCE_AWARE_AMOUNT_RE.finditer(body):
        amount = _parse_source_aware_amount_token(match.group(0))
        if amount is not None:
            matches.append(match)
    return matches


def _split_source_aware_recipient_and_narration(
    target: str,
    *,
    allow_bare_for_narration: bool,
) -> tuple[str, str | None] | None:
    text = _clean_slot_text(target)
    if not text:
        return None

    narration: str | None = None
    label_match = _SOURCE_AWARE_NARRATION_LABEL_RE.search(text)
    if label_match:
        narration = _clean_narration(label_match.group("narration"))
        text = _clean_slot_text(text[: label_match.start()])
    elif allow_bare_for_narration:
        bare_for = re.search(r"\s+for\s+(?P<narration>.+)$", text, flags=re.IGNORECASE)
        if bare_for:
            narration = _clean_narration(bare_for.group("narration"))
            text = _clean_slot_text(text[: bare_for.start()])

    recipient = _clean_slot_text(re.sub(r"^(?:to|for)\s+", "", text, flags=re.IGNORECASE))
    if not recipient or len(recipient) < 2:
        return None
    if _SOURCE_AWARE_RECIPIENT_BLOCK_RE.search(recipient):
        return None
    if _SOURCE_AWARE_RECIPIENT_MULTI_RE.search(recipient):
        return None
    if _SOURCE_AWARE_AMOUNT_RE.search(recipient):
        return None
    return recipient, narration


def _parse_source_aware_body(body: str) -> tuple[float, str, str | None] | None:
    matches = _source_aware_amount_matches(body)
    if len(matches) != 1:
        return None

    amount_match = matches[0]
    amount = _parse_source_aware_amount_token(amount_match.group(0))
    if amount is None:
        return None

    before_amount = _clean_slot_text(body[: amount_match.start()])
    after_amount = _clean_slot_text(body[amount_match.end() :])
    if before_amount:
        split = _split_source_aware_recipient_and_narration(
            before_amount,
            allow_bare_for_narration=True,
        )
        if split is None:
            return None
        recipient, pre_amount_narration = split
        post_amount_narration = None
        if after_amount:
            post_amount_narration = _clean_narration(
                re.sub(
                    r"^(?:for|narration|remark|memo|note|description)\s*[:\-]?\s+",
                    "",
                    after_amount,
                    flags=re.IGNORECASE,
                )
            )
            if post_amount_narration is None:
                return None
        return amount, recipient, post_amount_narration or pre_amount_narration

    split = _split_source_aware_recipient_and_narration(
        after_amount,
        allow_bare_for_narration=after_amount.lower().startswith("to "),
    )
    if split is None:
        return None
    recipient, narration = split
    return amount, recipient, narration


def parse_source_aware_transfer_direct(
    message_text: str,
    *,
    accounts: list[dict[str, Any]],
) -> SourceAwareTransferDirectParse | None:
    """Parse high-confidence source-bank single transfer commands.

    This is deliberately narrow. False negatives fall back to the planner; false
    positives would move money from the wrong account.
    """

    normalized = re.sub(r"\s+", " ", message_text.strip()).rstrip("?.!,")
    if not normalized or not accounts:
        return None
    normalized_lower = normalized.lower()
    if _PHONE_NUMBER_CUE_RE.search(normalized_lower):
        return None
    if _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized_lower):
        return None
    if _looks_like_multi_recipient_transfer(normalized_lower):
        return None

    source_first_match = _SOURCE_AWARE_SOURCE_FIRST_RE.match(normalized)
    if source_first_match:
        source_bank_name = _match_unique_source_bank(accounts, source_first_match.group("source"))
        if source_bank_name is None:
            return None
        parsed_body = _parse_source_aware_body(source_first_match.group("body"))
        if parsed_body is None:
            return None
        amount, recipient_name, narration = parsed_body
        return SourceAwareTransferDirectParse(
            amount=amount,
            recipient_name=recipient_name,
            source_bank_name=source_bank_name,
            narration=narration,
        )

    from_source_match = _SOURCE_AWARE_VERB_AMOUNT_FROM_SOURCE_RE.match(normalized)
    if from_source_match:
        source_bank_name = _match_unique_source_bank(accounts, from_source_match.group("source"))
        if source_bank_name is None:
            return None
        parsed_amount = _parse_source_aware_amount_token(from_source_match.group("amount"))
        split = _split_source_aware_recipient_and_narration(
            from_source_match.group("target"),
            allow_bare_for_narration=True,
        )
        if parsed_amount is None or split is None:
            return None
        recipient_name, narration = split
        return SourceAwareTransferDirectParse(
            amount=parsed_amount,
            recipient_name=recipient_name,
            source_bank_name=source_bank_name,
            narration=narration,
        )

    to_target_from_source_match = _SOURCE_AWARE_VERB_AMOUNT_TO_TARGET_FROM_SOURCE_RE.match(normalized)
    if to_target_from_source_match:
        source_bank_name = _match_unique_source_bank(accounts, to_target_from_source_match.group("source"))
        if source_bank_name is None:
            return None
        parsed_amount = _parse_source_aware_amount_token(to_target_from_source_match.group("amount"))
        split = _split_source_aware_recipient_and_narration(
            to_target_from_source_match.group("target"),
            allow_bare_for_narration=True,
        )
        if parsed_amount is None or split is None:
            return None
        recipient_name, narration = split
        return SourceAwareTransferDirectParse(
            amount=parsed_amount,
            recipient_name=recipient_name,
            source_bank_name=source_bank_name,
            narration=narration,
        )

    return None


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
    "parse_source_aware_transfer_direct",
    "SourceAwareTransferDirectParse",
]
