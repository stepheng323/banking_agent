"""WhatsApp client for sending messages and flows."""
import asyncio
import json
from typing import Any, Dict
import httpx
from shared.config.settings import settings


GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppClient:
    """WhatsApp client for sending messages and flows."""
    def __init__(self):
        self.access_token = settings.meta_access_token
        self.phone_number_id = settings.meta_phone_number_id
        self._validate_config()

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
                    print("❌ WhatsApp API Authentication Failed (401)")
                    print("   Check your META_ACCESS_TOKEN")
                    raise
                elif attempt < max_retries:
                    print(
                        f"⚠️  HTTP {e.response.status_code} error (attempt {attempt}/{max_retries}), retrying..."
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    print(
                        f"❌ Max retries reached. Final error: {e.response.status_code}")
                    # Print error response for debugging
                    try:
                        error_body = e.response.json()
                        print(f"   Error response: {error_body}")
                    except:
                        print(f"   Error response: {e.response.text}")
                    raise

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    print(
                        f"⚠️  Connection failed (attempt {attempt}/{max_retries}): Network unreachable"
                    )

                    await asyncio.sleep(2 * attempt)
                else:
                    print(f"❌ Max retries reached. Connection failed: {e}")
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

    def _validate_config(self) -> None:
        """Validate WhatsApp client configuration."""
        errors = []

        if not self.access_token:
            errors.append("META_ACCESS_TOKEN is not set")
        elif self.access_token == "development_access_token":
            print(
                "⚠️  Using development META_ACCESS_TOKEN - messages will fail in production")

        if not self.phone_number_id:
            errors.append("META_PHONE_NUMBER_ID is not set")
        elif self.phone_number_id == "development_phone_id":
            print(
                "⚠️  Using development META_PHONE_NUMBER_ID - messages will fail in production")

        if errors:
            error_msg = "WhatsApp client configuration errors:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            raise ValueError(error_msg)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get_url(self) -> str:
        return f"{GRAPH_API_BASE}/{self.phone_number_id}/messages"

    async def send_text(self, to: str, text: str, preview_url: bool = False) -> Dict[str, Any]:
        """Send a text message to a WhatsApp number."""
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
        """Send a typing indicator to a WhatsApp number."""
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
        footer: str = '',
        flow_token: str = '',
        flow_action_payload: Dict[str, Any] = {},
    ) -> Dict[str, Any]:
        """Send a flow to a WhatsApp number."""
        url = self._get_url()

        interactive_payload = {
            "type": "flow",
            "header": {"type": "text", "text": header},
            "body": {"text": text_body},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": flow_token or "",
                    "flow_id": flow_id,
                    "flow_cta": flow_cta,
                    "flow_action": "navigate",
                    "flow_action_payload": flow_action_payload or {"screen": screen_name},
                },
            },
        }
        
        # Only include footer if it's not empty (WhatsApp requires footer text to have at least 1 character)
        if footer and footer.strip():
            interactive_payload["footer"] = {"text": footer}
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive_payload,
        }

        try:

            result = await self._send(url, payload)
            return result
        except Exception as e:
            print(f"❌ Failed to send flow: {e}")

            print(f"   Payload was: {json.dumps(payload, indent=2)}")
            raise
