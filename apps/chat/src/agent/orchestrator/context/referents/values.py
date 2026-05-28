"""Safe value helpers for referent memory."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_FRAGMENTS = ("pin", "otp", "password", "token", "secret", "auth", "callback")


def safe_scalar(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int | float | bool):
        return value
    return None


def safe_data(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for raw_key, value in values.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
            continue
        scalar = safe_scalar(value)
        if scalar is not None:
            safe[key] = scalar
            continue
        if isinstance(value, list):
            safe_items = [safe_scalar(item) for item in value[:10]]
            safe_list = [item for item in safe_items if item is not None]
            if safe_list:
                safe[key] = safe_list
        elif isinstance(value, dict):
            nested = safe_data(value)
            if nested:
                safe[key] = nested
    return safe


def first_text(*values: Any) -> str | None:
    for value in values:
        safe = safe_scalar(value)
        if safe is not None:
            return str(safe)
    return None


def first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
