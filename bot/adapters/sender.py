import httpx
from bot.core.config import settings

GRAPH_BASE = "https://graph.facebook.com/v21.0"


async def send_text(to: str, text: str) -> None:
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
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
