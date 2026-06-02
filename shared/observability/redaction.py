"""Redaction helpers for logs, readiness payloads, and trace metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from shared.utils.json import to_json_safe
from shared.utils.logging import log_fingerprint

_SENSITIVE_KEY_FRAGMENTS = (
    "account_number",
    "recipient_account",
    "source_account",
    "mandate",
    "pin",
    "otp",
    "password",
    "token",
    "secret",
    "authorization",
    "auth_code",
    "api_key",
    "access_token",
    "refresh_token",
    "private_key",
    "payload",
    "raw_body",
    "base64",
    "image_url",
    "media",
)

_TEN_DIGIT_ACCOUNT_RE = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_LONG_PHONE_RE = re.compile(r"(?<!\d)(?:\+?234|0)?[789]\d{9}(?!\d)")
_PIN_OTP_RE = re.compile(r"\b(?:pin|otp|password|passcode|token|secret)\s*[:=]?\s*([A-Za-z0-9_\-]{4,})", re.I)
_DATA_URL_RE = re.compile(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]+")
_PROVIDER_REFERENCE_RE = re.compile(
    r"\b(?:ref|reference|provider_reference|mandate_id)\s*[:=]\s*([A-Za-z0-9_.:\-]{8,})",
    re.I,
)


def fingerprint(value: Any) -> str:
    """Return a short stable fingerprint for sensitive values."""
    return log_fingerprint(value)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.strip().lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_string(value: str) -> str:
    text = _DATA_URL_RE.sub("[redacted_base64_media]", value)
    text = _PIN_OTP_RE.sub(lambda match: match.group(0).replace(match.group(1), "[redacted]"), text)
    text = _PROVIDER_REFERENCE_RE.sub(
        lambda match: match.group(0).replace(match.group(1), f"hash:{fingerprint(match.group(1))}"),
        text,
    )
    text = _TEN_DIGIT_ACCOUNT_RE.sub(lambda match: f"****{match.group(1)[-4:]}", text)
    text = _LONG_PHONE_RE.sub(lambda match: f"hash:{fingerprint(match.group(0))}", text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively produce a JSON-safe, redacted value."""
    safe = to_json_safe(value)
    if isinstance(safe, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in safe.items():
            key_text = str(key)
            if _key_is_sensitive(key_text):
                if item in (None, ""):
                    redacted[key_text] = item
                elif "account" in key_text.lower() and isinstance(item, str):
                    redacted[key_text] = _TEN_DIGIT_ACCOUNT_RE.sub(lambda match: f"****{match.group(1)[-4:]}", item)
                    if redacted[key_text] == item:
                        redacted[key_text] = f"hash:{fingerprint(item)}"
                else:
                    redacted[key_text] = f"hash:{fingerprint(item)}"
                continue
            redacted[key_text] = redact_value(item)
        return redacted
    if isinstance(safe, Sequence) and not isinstance(safe, str):
        return [redact_value(item) for item in safe]
    if isinstance(safe, str):
        return _redact_string(safe)
    return safe


def redacted_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a redacted JSON-safe dict."""
    redacted = redact_value(dict(value or {}))
    return redacted if isinstance(redacted, dict) else {}
