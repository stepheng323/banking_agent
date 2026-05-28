"""WhatsApp media upload and download helpers."""

from typing import Any

import httpx

from shared.clients.whatsapp.endpoints import media_metadata_url, media_upload_url
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def upload_media_from_url(
    *,
    http_client: httpx.AsyncClient,
    phone_number_id: str,
    headers: dict[str, str],
    media_url: str,
) -> str:
    payload = {
        "messaging_product": "whatsapp",
        "url": media_url,
        "type": "image",
    }

    try:
        resp = await http_client.post(media_upload_url(phone_number_id), headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        media_id: str | None = result.get("id")
        if not media_id:
            raise ValueError("No media ID returned from WhatsApp")
        logger.info("whatsapp_media_uploaded", media_id_hash=log_fingerprint(media_id))
        return media_id
    except Exception as e:
        logger.error(
            "whatsapp_media_upload_failed",
            media_url_hash=log_fingerprint(media_url),
            error_type=type(e).__name__,
        )
        raise


async def upload_buffer(
    *,
    http_client: httpx.AsyncClient,
    access_token: str,
    phone_number_id: str,
    data: bytes,
    filename: str,
    mime_type: str = "application/pdf",
) -> str:
    try:
        files: dict[str, tuple[str | None, bytes | str, str] | tuple[str | None, str]] = {
            "file": (filename, data, mime_type),
            "messaging_product": (None, "whatsapp"),
            "type": (None, mime_type),
        }
        headers = {"Authorization": f"Bearer {access_token}"}

        resp = await http_client.post(
            media_upload_url(phone_number_id),
            headers=headers,
            files=files,
            timeout=60,
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        media_id: str | None = result.get("id")
        if not media_id:
            raise ValueError("No media ID returned from WhatsApp")
        logger.info(
            "whatsapp_buffer_uploaded",
            media_id_hash=log_fingerprint(media_id),
            filename_hash=log_fingerprint(filename),
        )
        return media_id
    except Exception as e:
        logger.error(
            "whatsapp_buffer_upload_failed",
            filename_hash=log_fingerprint(filename),
            error_type=type(e).__name__,
        )
        raise


async def get_media_url(
    *,
    http_client: httpx.AsyncClient,
    headers: dict[str, str],
    media_id: str,
) -> str:
    try:
        resp = await http_client.get(media_metadata_url(media_id), headers=headers, timeout=10)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        media_url: str | None = result.get("url")
        if not media_url:
            raise ValueError("No URL returned for media")
        return media_url
    except Exception as e:
        logger.error(
            "whatsapp_media_url_failed",
            media_id_hash=log_fingerprint(media_id),
            error_type=type(e).__name__,
        )
        raise


async def download_media(
    *,
    http_client: httpx.AsyncClient,
    headers: dict[str, str],
    media_url: str,
) -> bytes:
    try:
        resp = await http_client.get(media_url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error(
            "whatsapp_media_download_failed",
            media_url_hash=log_fingerprint(media_url),
            error_type=type(e).__name__,
        )
        raise
