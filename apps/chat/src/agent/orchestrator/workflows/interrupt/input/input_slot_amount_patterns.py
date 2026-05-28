"""Amount-shaped input slot shortcut patterns."""

import re

_INPUT_SIMPLE_AMOUNT_REPLY_RE = re.compile(r"^(?:₦?\d[\d,]*(?:\.\d+)?k?|all|everything|half|50%)$", re.IGNORECASE)
_INPUT_AMOUNT_COMMAND_REPLY_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|transfer|pay|remit)\s+"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\s*[.!?]?$",
    re.IGNORECASE,
)
_INPUT_NUMERIC_AMOUNT_REPLY_RE = re.compile(
    r"^(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\s*(?:naira|ngn)?\s*[.!?]?$",
    re.IGNORECASE,
)


__all__ = [
    "_INPUT_AMOUNT_COMMAND_REPLY_RE",
    "_INPUT_NUMERIC_AMOUNT_REPLY_RE",
    "_INPUT_SIMPLE_AMOUNT_REPLY_RE",
]
