import httpx

from apps.gateway.core.config import settings
from shared.utils.logging import get_logger, log_fingerprint

GRAPH_BASE = "https://graph.facebook.com/v21.0"
logger = get_logger(__name__)


async def send_text(to: str, text: str) -> None:
    """Send a text message via WhatsApp Business API."""
    url = f"{GRAPH_BASE}/{settings.whatsapp.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp.access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            logger.info("legacy_whatsapp_message_sent", to_hash=log_fingerprint(to))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            logger.error("legacy_whatsapp_auth_failed", http_status=401)
        else:
            logger.error("legacy_whatsapp_api_failed", http_status=e.response.status_code)
        raise
    except Exception as e:
        logger.error("legacy_whatsapp_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        raise
