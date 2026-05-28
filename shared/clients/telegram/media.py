"""Telegram media URL and download helpers."""

from typing import Any, Protocol

import httpx

from shared.utils.logging import get_logger, log_fingerprint

TELEGRAM_FILE_API_BASE = "https://api.telegram.org/file"
logger = get_logger(__name__)


class TelegramApiCall(Protocol):
    async def __call__(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]: ...


async def get_media_url(
    *,
    api_call: TelegramApiCall,
    bot_token: str,
    media_id: str,
) -> str:
    try:
        result = await api_call("getFile", {"file_id": media_id})
        file_path = result.get("result", {}).get("file_path")
        if not file_path:
            raise ValueError(f"Could not get file_path for media {media_id}")
        return f"{TELEGRAM_FILE_API_BASE}/bot{bot_token}/{file_path}"
    except Exception as e:
        logger.error(
            "telegram_media_url_failed",
            media_id_hash=log_fingerprint(media_id),
            error_type=type(e).__name__,
        )
        raise


async def download_media(
    *,
    http_client: httpx.AsyncClient,
    media_url: str,
) -> bytes:
    try:
        resp = await http_client.get(media_url)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error(
            "telegram_media_download_failed",
            media_url_hash=log_fingerprint(media_url),
            error_type=type(e).__name__,
        )
        raise
