"""Recipient bank-detail shape checks used by gate classifiers."""

import re

_BANK_DETAILS_ACCOUNT_LABEL_RE = re.compile(
    r"\b(?:account\s*(?:number|no\.?|#)?|acct(?:\s*(?:number|no\.?))?|a/c)\b"
    r"\s*[:\-]?\s*(?P<account>(?:\d[\s,.\-]?){10,11})(?!\d)",
    re.IGNORECASE,
)
_BANK_DETAILS_BANK_LABEL_RE = re.compile(
    r"(?:^|\n)\s*bank(?:\s+name)?\s*[:\-]\s*(?P<bank>[^\n;]+)",
    re.IGNORECASE,
)
_BANK_DETAILS_ACCOUNT_FIRST_RE = re.compile(
    r"^\s*(?P<account>(?:\d[\s,.\-]?){10,11})\s*(?:[,;:\-–—]|\s)\s*(?P<bank>[A-Za-z][A-Za-z0-9 &'._-]{1,80})\s*$",
    re.IGNORECASE,
)
_BANK_DETAILS_BANK_FIRST_RE = re.compile(
    r"^\s*(?P<bank>[A-Za-z][A-Za-z0-9 &'._-]{1,80})\s*(?:[,;:\-–—]|\s)\s*(?P<account>(?:\d[\s,.\-]?){10,11})\s*$",
    re.IGNORECASE,
)
_BANK_DETAILS_NON_BANK_RE = re.compile(r"\b(?:send|transfer|pay|remit)\b", re.IGNORECASE)


def _has_recipient_bank_details_shape(message_text: str) -> bool:
    text = message_text.strip()
    if not text:
        return False

    account_match = _BANK_DETAILS_ACCOUNT_LABEL_RE.search(text)
    bank_match = _BANK_DETAILS_BANK_LABEL_RE.search(text)
    if account_match and bank_match:
        account = re.sub(r"\D+", "", account_match.group("account"))
        bank = bank_match.group("bank").strip(" \t\r\n*_`.,;:")
        return len(account) == 10 and bool(bank) and not bank.isdigit() and not _BANK_DETAILS_NON_BANK_RE.search(bank)

    compact_text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    for pattern in (_BANK_DETAILS_ACCOUNT_FIRST_RE, _BANK_DETAILS_BANK_FIRST_RE):
        match = pattern.match(compact_text)
        if not match:
            continue
        account = re.sub(r"\D+", "", match.group("account"))
        bank = match.group("bank").strip(" \t\r\n*_`.,;:")
        if len(account) == 10 and bank and not bank.isdigit() and not _BANK_DETAILS_NON_BANK_RE.search(bank):
            return True

    return False


__all__ = ["_has_recipient_bank_details_shape"]
