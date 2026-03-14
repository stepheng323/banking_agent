import httpx

from apps.gateway.core.config import settings
from shared.utils.logging import get_logger

GRAPH_BASE = "https://graph.facebook.com/v21.0"
logger = get_logger(__name__)


async def send_text(to: str, text: str) -> None:
    """Send a text message via WhatsApp Business API."""
    if not settings.enable_outbound_sender:
        logger.info(
            "gateway_sender_skipped",
            channel="whatsapp",
            to=to,
            reason="outbound_sender_disabled",
        )
        return
    url = f"{GRAPH_BASE}/{settings.meta_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.meta_access_token}",
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
            print(f"✓ Message sent successfully to {to}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print("❌ WhatsApp API Authentication Failed (401)")
            print("   Check your META_ACCESS_TOKEN in .env")
            print("   Token may be expired or invalid")
        else:
            print(f"❌ WhatsApp API Error: {e.response.status_code}")
            print(f"   Response: {e.response.text}")
        raise
    except Exception as e:
        print(f"❌ Failed to send message: {e}")
        raise
