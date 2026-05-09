"""Telegram Web App authentication dependency."""

import hashlib
import hmac
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int | None = None,
) -> dict:
    """Validate the Telegram Mini App initData cryptographic string."""
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))

        if "hash" not in parsed_data:
            raise ValueError("Missing hash parameter")
        if "auth_date" not in parsed_data:
            raise ValueError("Missing auth_date parameter")

        received_hash = parsed_data.pop("hash")

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Cryptographic hash mismatch")

        if max_age_seconds is not None and max_age_seconds > 0:
            try:
                auth_date = int(parsed_data["auth_date"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid auth_date parameter") from exc
            age_seconds = int(time.time()) - auth_date
            if age_seconds < 0:
                raise ValueError("auth_date is in the future")
            if age_seconds > max_age_seconds:
                raise ValueError("Telegram init data expired")

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
        return validate_telegram_init_data(
            telegam_init_data,
            bot_token,
            max_age_seconds=settings.telegram_init_data_max_age_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
