"""Direct account, query, and beneficiary domain classifiers."""

import re

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.bank_details import (
    _has_recipient_bank_details_shape,
)

ACCOUNT_BALANCE_REQUEST_PATTERNS = (
    r"\bbalance\b",
    r"\baccount\s+balance\b",
    r"\bcheck\s+my\s+balance\b",
    r"\bwhat(?:'s| is)\s+my\s+balance\b",
    r"\bhow\s+much\s+do\s+i\s+have\b",
    r"\bhow\s+much\s+is\s+in\s+my\s+account\b",
)
ACCOUNT_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get|display|tell me)\s+)?(?:all\s+)?(?:my\s+)?(?:linked\s+)?accounts?\b",
    r"^what\s+(?:linked\s+)?accounts?\s+do\s+i\s+have\b",
    r"^how\s+many\s+accounts?\s+do\s+i\s+have\b",
    r"^do\s+i\s+have\s+any\s+(?:linked\s+)?accounts?\b",
    r"^(?:link|add)\s+(?:a\s+)?(?:new\s+|another\s+)?account\b",
    r"^(?:unlink|remove|disconnect)\s+(?:my\s+)?account\b",
    r"^(?:set|make)\s+.+\s+default\b",
)
BENEFICIARY_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?beneficiar(?:y|ies)\b",
    r"^who\s+do\s+i\s+have\s+saved\b",
    r"^(?:show|list|view|get)\s+(?:my\s+)?saved\s+(?:recipients?|beneficiar(?:y|ies))\b",
    r"^how\s+many\s+beneficiar(?:y|ies)\s+do\s+i\s+have\b",
    r"^do\s+i\s+have\s+any\s+beneficiar(?:y|ies)\b",
)
BALANCE_DIRECT_TRANSACTION_HINT_PATTERNS = (r"\b(send|transfer|pay|buy|airtime|data|bundle|fund|withdraw)\b",)
BALANCE_DIRECT_CANCEL_PREFIX_RE = re.compile(
    r"^(?:cancel|abort|stop|nevermind|never\s+mind)(?:\s+(?:and|then))?\s+",
    re.IGNORECASE,
)
GENERIC_ACCOUNT_BALANCE_REQUEST_PATTERNS = (
    r"^(?:please\s+)?(?:check|show|view|get|display)\s+(?:my\s+)?(?:account\s+)?balance$",
    r"^(?:please\s+)?(?:tell\s+me\s+)?what(?:'s| is)\s+my\s+(?:account\s+)?balance$",
    r"^(?:my\s+)?(?:account\s+)?balance$",
    r"^how\s+much\s+do\s+i\s+have$",
    r"^how\s+much\s+is\s+in\s+my\s+account$",
)
_QUERY_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:recent\s+|latest\s+)?(?:transactions?|transaction\s+history|history|statement)",
    r"^(?:show|list|view|get)\s+(?:my\s+)?(?:failed|pending|successful|reversed)\s+(?:transactions?|transfers?|payments?)\b",
    r"^(?:show|list|view|get)\s+(?:my\s+)?"
    r"(?:gtb|gtbank|access|zenith|wema|uba|opay|kuda|moniepoint|palmpay|first bank|fcmb|stanbic|"
    r"sterling|union|fidelity|keystone|providus|polaris)\s+(?:transactions|transaction\s+history|history|statement)\b",
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+\d+\s+transactions?",
    r"^how\s+much\s+(?:(?:total|in\s+total)\s+)?(?:did|have)\s+i\s+(?:spend|spent|send|sent|pay|paid|receive|received)",
    r"^how\s+much\s+(?:came|come)\s+in\b",
    r"^how\s+much\s+(?:money\s+)?(?:entered|was\s+received|got\s+credited)\b",
    r"^(?:what(?:'s| is|'s)|how\s+much\s+is)\s+my\s+(?:spending|expenses?|income|inflow)",
    r"^who\s+did\s+i\s+(?:send|transfer|pay)\s+(?:money\s+)?to",
    r"^who\s+sent\s+me\s+(?:the\s+most\s+)?(?:money\s+)?",
    r"^where\s+did\s+my\s+money\s+go\b",
    r"^what\s+did\s+i\s+spend\s+(?:on|money\s+on)\b",
    r"^(?:top|my)\s+(?:recipients?|beneficiar)",
    r"^(?:can|could)\s+i\s+(?:afford|send|transfer|pay|spend|cover)\s+.+",
)
_STRUCTURAL_QUERY_DIRECT_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:recent\s+|latest\s+)?(?:transactions?|transaction\s+history|history|statement)\b",
    r"^(?:show|list|view|get)\s+(?:my\s+)?(?:failed|pending|successful|reversed)\s+(?:transactions?|transfers?|payments?)\b",
    r"^(?:show|list|view|get)\s+(?:my\s+)?"
    r"(?:gtb|gtbank|access|zenith|wema|uba|opay|kuda|moniepoint|palmpay|first bank|fcmb|stanbic|"
    r"sterling|union|fidelity|keystone|providus|polaris)\s+(?:transactions|transaction\s+history|history|statement)\b",
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+\d+\s+transactions?\b",
    r"^(?:can|could)\s+i\s+(?:afford|send|transfer|pay|spend|cover)\s+.+",
    r"^how\s+much\s+(?:came|come)\s+in\b",
    r"^who\s+sent\s+me\s+(?:the\s+most\s+)?(?:money\s+)?",
    r"^where\s+did\s+my\s+money\s+go\b",
)


def _is_account_balance_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    candidate = BALANCE_DIRECT_CANCEL_PREFIX_RE.sub("", normalized)
    if any(re.search(pattern, candidate) for pattern in BALANCE_DIRECT_TRANSACTION_HINT_PATTERNS):
        return False
    return any(re.search(pattern, candidate) for pattern in ACCOUNT_BALANCE_REQUEST_PATTERNS)


def _is_generic_account_balance_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    candidate = BALANCE_DIRECT_CANCEL_PREFIX_RE.sub("", normalized)
    return any(re.search(pattern, candidate) for pattern in GENERIC_ACCOUNT_BALANCE_REQUEST_PATTERNS)


def _is_query_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _QUERY_DOMAIN_PATTERNS)


def _is_structural_query_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _STRUCTURAL_QUERY_DIRECT_PATTERNS)


def _is_account_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    if _has_recipient_bank_details_shape(message_text):
        return False
    if _is_account_balance_request(normalized):
        return False
    if _is_query_domain_request(normalized):
        return False
    return any(re.search(pattern, normalized) for pattern in ACCOUNT_DOMAIN_PATTERNS)


def _is_beneficiary_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in BENEFICIARY_DOMAIN_PATTERNS)


__all__ = [
    "_is_account_balance_request",
    "_is_account_domain_request",
    "_is_beneficiary_domain_request",
    "_is_generic_account_balance_request",
    "_is_query_domain_request",
    "_is_structural_query_domain_request",
]
