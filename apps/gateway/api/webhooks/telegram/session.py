"""Telegram Mini App session helpers."""

import hashlib
import json
from typing import Any


def token_fingerprint(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def telegram_init_user_id(user_data: dict) -> str:
    raw_user = user_data.get("user")
    if isinstance(raw_user, str):
        try:
            parsed = json.loads(raw_user)
        except json.JSONDecodeError:
            return ""
        return str(parsed.get("id") or "")
    if isinstance(raw_user, dict):
        return str(raw_user.get("id") or "")
    return ""


def invalid_telegram_session_error() -> dict[str, Any]:
    return {"success": False, "error": "Invalid or expired session. Please reopen this page from Telegram."}
