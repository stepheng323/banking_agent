import httpx
from typing import Dict, Any
import os
import asyncio


GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppClient:
    def __init__(self):
        pass

    access_token = os.getenv("META_ACCESS_TOKEN")
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID")

    async def _send(
        self, url: str, payload: Dict[str, Any], max_retries: int = 3
    ) -> Dict[str, Any]:

        headers = self._get_headers()
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    result = resp.json()
                    print(f"✅ Request successful (attempt {attempt})")
                    return result

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 401:
                    print(f"❌ WhatsApp API Authentication Failed (401)")
                    print(f"   Check your META_ACCESS_TOKEN")
                    raise
                elif attempt < max_retries:
                    print(
                        f"⚠️  HTTP {e.response.status_code} error (attempt {attempt}/{max_retries}), retrying..."
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    print(
                        f"❌ Max retries reached. Final error: {e.response.status_code}"
                    )
                    raise

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    print(f"⚠️  Error on attempt {attempt}/{max_retries}: {e}")
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Max retries reached. Final error: {e}")
                    raise

        if last_error:
            raise last_error
        return {}

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get_url(self) -> str:
        return f"{GRAPH_API_BASE}/{self.phone_number_id}/messages"

    async def send_text(
        self, to: str, text: str, preview_url: bool = False
    ) -> Dict[str, Any]:
        url = self._get_url()

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }

        try:
            result = await self._send(url, payload)
            print(f"✅ Text message sent to {to}")
            return result
        except Exception as e:
            print(f"❌ Failed to send text message: {e}")
            raise

    async def send_typing_indicator(self, message_id: str) -> Dict[str, Any]:
        url = self._get_url()

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }

        try:
            result = await self._send(url, payload, max_retries=1)
            return result
        except Exception as e:
            print(f"⚠️  Failed to send typing indicator (non-critical): {e}")
            return {}

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_cta: str,
        screen_name: str,
        header: str,
        text_body: str,
        footer: str = None,
        flow_token: str = None,
        flow_action_payload: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        url = self._get_url()

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "header": {"type": "text", "text": header},
                "body": {"text": text_body},
                "footer": {"text": footer},
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_token": flow_token,
                        "flow_id": flow_id,
                        "flow_cta": flow_cta,
                        "flow_action": "navigate",
                        "flow_action_payload": flow_action_payload
                        or {"screen": screen_name},
                    },
                },
            },
        }

        try:
            result = await self._send(url, payload)
            print(f"✅ Flow sent to {to} (Flow ID: {flow_id})")
            return result
        except Exception as e:
            print(f"❌ Failed to send flow: {e}")
            raise
