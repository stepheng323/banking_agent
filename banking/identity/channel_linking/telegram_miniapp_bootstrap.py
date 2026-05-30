"""Short-lived Telegram Mini App bootstrap tokens."""

import json
import secrets
from dataclasses import dataclass
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

BOOTSTRAP_TTL_SECONDS = 300
BOOTSTRAP_ENDPOINTS = frozenset({"onboarding", "linking", "pin"})
_KEY_PREFIX = "telegram:miniapp:bootstrap"


@dataclass(frozen=True, slots=True)
class TelegramMiniAppBootstrap:
    """Server-side Mini App bootstrap payload."""

    flow_token: str
    chat_id: str
    endpoint: str
    extra: dict[str, Any]


def _key(nonce: str) -> str:
    return f"{_KEY_PREFIX}:{nonce}"


def _normalize_endpoint(endpoint: str) -> str:
    normalized = str(endpoint or "").strip().lower()
    if normalized not in BOOTSTRAP_ENDPOINTS:
        raise ValueError("Invalid Telegram Mini App bootstrap endpoint")
    return normalized


async def create_telegram_miniapp_bootstrap(
    *,
    chat_id: str,
    flow_token: str,
    endpoint: str,
    extra: dict[str, Any] | None = None,
    ttl_seconds: int = BOOTSTRAP_TTL_SECONDS,
) -> str:
    """Create a short-lived bootstrap nonce bound to a Telegram chat and page type."""
    normalized_endpoint = _normalize_endpoint(endpoint)
    nonce = secrets.token_urlsafe(32)
    payload = {
        "chat_id": str(chat_id),
        "flow_token": str(flow_token),
        "endpoint": normalized_endpoint,
        "extra": extra or {},
    }
    redis = RedisClient.get_client()
    await redis.set(
        _key(nonce),
        json.dumps(payload, separators=(",", ":")),
        ex=min(ttl_seconds, settings.telegram_init_data_max_age_seconds),
    )
    logger.info(
        "telegram_miniapp_bootstrap_created",
        nonce_hash=log_fingerprint(nonce),
        flow_token_hash=log_fingerprint(flow_token),
        chat_id_hash=log_fingerprint(chat_id),
        endpoint=normalized_endpoint,
    )
    return nonce


async def consume_telegram_miniapp_bootstrap(
    *,
    nonce: str,
    endpoint: str,
    init_user_id: str,
) -> TelegramMiniAppBootstrap | None:
    """Resolve a bootstrap nonce after verifying endpoint type and Telegram owner.

    Telegram WebViews can reload a Mini App URL with the same query string, so the
    nonce is intentionally reusable until its short Redis TTL expires.
    """
    normalized_endpoint = _normalize_endpoint(endpoint)
    token = str(nonce or "").strip()
    if not token:
        return None

    redis = RedisClient.get_client()
    key = _key(token)
    raw: Any = await redis.get(key)

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not raw:
        logger.warning("telegram_miniapp_bootstrap_missing", nonce_hash=log_fingerprint(token))
        return None

    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        logger.warning("telegram_miniapp_bootstrap_malformed", nonce_hash=log_fingerprint(token))
        return None

    record_endpoint = str(payload.get("endpoint") or "").strip().lower()
    record_chat_id = str(payload.get("chat_id") or "")
    if record_endpoint != normalized_endpoint or record_chat_id != str(init_user_id):
        logger.warning(
            "telegram_miniapp_bootstrap_rejected",
            nonce_hash=log_fingerprint(token),
            endpoint=normalized_endpoint,
            record_endpoint=record_endpoint,
            chat_id_hash=log_fingerprint(init_user_id),
            record_chat_id_hash=log_fingerprint(record_chat_id),
        )
        return None

    return TelegramMiniAppBootstrap(
        flow_token=str(payload.get("flow_token") or ""),
        chat_id=record_chat_id,
        endpoint=record_endpoint,
        extra=payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
    )
