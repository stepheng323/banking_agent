"""Telegram Web App authentication dependency."""

import hashlib
import hmac
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict:
    """Validate the Telegram Mini App initData cryptographic string."""
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))

        if "hash" not in parsed_data:
            raise ValueError("Missing hash parameter")

        received_hash = parsed_data.pop("hash")

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            raise ValueError("Cryptographic hash mismatch")

        return parsed_data

    except Exception as e:
        logger.warning("telegram_init_data_validation_failed", error=str(e), init_data_provided=bool(init_data))
        raise ValueError(f"Invalid init data: {e}")


async def verify_telegram_init_data(
    telegam_init_data: str | None = Header(default=None, alias="Telegram-Init-Data"),
) -> dict:
    """FastAPI Dependency to verify Telegram-Init-Data header."""
    if not telegam_init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram-Init-Data header")

    bot_token = settings.telegram_bot_token
    if not bot_token:
        logger.error("telegram_bot_token_missing")
        raise HTTPException(status_code=500, detail="Telegram bot token not configured")

    try:
        return validate_telegram_init_data(telegam_init_data, bot_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
