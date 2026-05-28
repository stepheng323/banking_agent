"""Small deterministic signals used by pending-interrupt handling."""

import re
from typing import Any

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320
INPUT_INTERRUPT_MAX_ATTEMPTS = 3

_SCHEDULE_INTERRUPT_READ_CANDIDATE_RE = re.compile(
    r"(?iu)(?:"
    r"\bschedul\w*\b|\brecurr\w*\b|\bpending\b.*\b(?:transaction|payment|transfer|airtime|data)\w*\b|"
    r"\bprogram(?:me|med|mes|ar|ado|ada|ados|adas|mé|mée|mées|més)\w*\b|"
    r"\betal[eè]\b|\betalement\b|\bprogramm[ée]s?\b|"
    r"\beto\b.*\b(?:isanwo|owo|transaction)\b|"
    r"\b(?:ti a se eto|san nigbamii|sisanwo ti n bo)\b|"
    r"\b(?:tsara|jadawali|maimaitawa|biyan)\b.*\b(?:kudi|ciniki|biya)\b|"
    r"\b(?:haziri|ugwo|mbufe|azumahia)\b.*\b(?:emechaa|na-abia|oge)\b"
    r")"
)
_ACCOUNT_BALANCE_INTERRUPT_RE = re.compile(
    r"\b(?:balance|account\s+balance|check\s+my\s+balance|"
    r"what(?:'s| is)?\s+my\s+.+?\bbalance\b|"
    r"how\s+much\s+(?:do\s+i\s+have|is\s+in\s+my\s+account))\b",
    re.IGNORECASE,
)
_ACCOUNT_BALANCE_TRANSACTION_HINT_RE = re.compile(
    r"\b(?:send|transfer|pay|buy|airtime|data|bundle|fund|withdraw)\b",
    re.IGNORECASE,
)
_INPUT_INTERRUPT_GREETING_RE = re.compile(
    r"(?iu)^\s*(?:hi+|hello|hey|good\s+(?:morning|afternoon|evening)|"
    r"how\s+far|sannu|ndewo|pele(?:\s+o)?|pẹlẹ(?:\s+o)?)\s*[.!?]*\s*$"
)


def _could_be_schedule_interrupt_read_request(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 180:
        return False
    return bool(_SCHEDULE_INTERRUPT_READ_CANDIDATE_RE.search(normalized))


def _is_account_balance_interrupt_switch(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower()).strip("?.!, ")
    if not normalized or len(normalized) > 180:
        return False
    if _ACCOUNT_BALANCE_TRANSACTION_HINT_RE.search(normalized):
        return False
    return bool(_ACCOUNT_BALANCE_INTERRUPT_RE.search(normalized))


def _is_input_interrupt_greeting(text: str) -> bool:
    return bool(_INPUT_INTERRUPT_GREETING_RE.fullmatch(text or ""))


def _input_interrupt_required_fields(interrupt: Any) -> set[str]:
    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    if not isinstance(fields_by_task, dict):
        return set()
    fields: set[str] = set()
    for task_fields in fields_by_task.values():
        if isinstance(task_fields, list):
            fields.update(str(field) for field in task_fields if isinstance(field, str))
    return fields


__all__ = [
    "INPUT_INTERRUPT_MAX_ATTEMPTS",
    "INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS",
    "INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS",
    "INTERRUPT_PROMPT_COMPACT_MAX_CHARS",
    "INTERRUPT_PROMPT_MAX_CHARS",
    "INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS",
    "INTERRUPT_REQUIRED_FIELDS_MAX_CHARS",
    "KNOWN_SWITCH_INTENTS",
    "NON_TRANSACTION_SWITCH_INTENTS",
    "TRANSACTION_INTENTS",
    "_could_be_schedule_interrupt_read_request",
    "_input_interrupt_required_fields",
    "_is_account_balance_interrupt_switch",
    "_is_input_interrupt_greeting",
]
