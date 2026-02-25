"""WhatsApp client for sending messages and flows."""

import asyncio
import json
from typing import Any

import httpx

from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.config.settings import settings

GRAPH_API_BASE = "https://graph.facebook.com/v24.0"


class WhatsAppClient(MessagingClient):
    """WhatsApp client for sending messages and flows."""

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def supports_flows(self) -> bool:
        return True

    def __init__(self):
        self.access_token = settings.meta_access_token
        self.phone_number_id = settings.meta_phone_number_id
        self._validate_config()

    async def _send(self, url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        headers = self._get_headers()
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    result: dict[str, Any] = resp.json()
                    print(f"✓ Request successful (attempt {attempt})")
                    return result

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 401:
                    print("❌ WhatsApp API Authentication Failed (401)")
                    print("   Check your META_ACCESS_TOKEN")
                    raise
                elif attempt < max_retries:
                    status = e.response.status_code
                    print(f"⚠️  HTTP {status} error (attempt {attempt}/{max_retries}), retrying...")
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Max retries reached. Final error: {e.response.status_code}")
                    try:
                        error_body = e.response.json()
                        print(f"   Error response: {error_body}")
                    except Exception:
                        print(f"   Error response: {e.response.text}")
                    raise

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    msg = f"⚠️  Connection failed (attempt {attempt}/{max_retries})"
                    print(f"{msg}: Network unreachable")

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
            print("⚠️  Using development META_ACCESS_TOKEN - messages will fail in production")

        if not self.phone_number_id:
            errors.append("META_PHONE_NUMBER_ID is not set")
        elif self.phone_number_id == "development_phone_id":
            print("⚠️  Using development META_PHONE_NUMBER_ID - messages will fail in production")

        if errors:
            error_msg = "WhatsApp client configuration errors:\n" + "\n".join(f"  - {error}" for error in errors)
            raise ValueError(error_msg)

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get_url(self) -> str:
        return f"{GRAPH_API_BASE}/{self.phone_number_id}/messages"

    async def _ensure_message_id(self, to: str, message_id: str | None) -> str | None:
        """Helper to get message_id from parameter or Redis for typing indicator support.

        Args:
            to: Recipient phone number
            message_id: Optional message_id provided by caller

        Returns:
            message_id if available (from parameter or Redis), None otherwise
        """
        if message_id:
            print(f"📨 Using provided message_id: {message_id[:20]}...")
            return message_id
        try:
            from shared.cache.redis_client import RedisClient

            redis_client = RedisClient.get_client()
            fetched_id = await redis_client.get(f"user:{to}:current_message_id")
            if fetched_id:
                print(f"📨 Fetched message_id from Redis: {fetched_id[:20]}...")
            else:
                print(f"⚠️ No message_id in Redis for {to}")
            return fetched_id
        except Exception as e:
            print(f"❌ Redis fetch failed for message_id: {e}")
            return None

    async def send_text(
        self,
        to: str,
        text: str,
        preview_url: bool = False,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message to a WhatsApp number.

        Args:
            to: Recipient phone number
            text: Message content
            preview_url: Whether to show URL preview
            message_id: If provided, send typing indicator. If None, auto-fetch from Redis.
        """
        url = self._get_url()

        # Always try to get message_id for typing indicator
        message_id = await self._ensure_message_id(to, message_id)

        if message_id:
            await self.send_typing_indicator(message_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }

        try:
            result = await self._send(url, payload)
            return result
        except Exception as e:
            print(f"Failed to send text message: {e}")
            raise

    async def send_typing_indicator(self, message_id: str) -> dict[str, Any]:
        """Send a typing indicator to a WhatsApp number.

        Can be called multiple times for the same message_id - each call resets the ~5s timer.
        """
        url = self._get_url()

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }

        try:
            result = await self._send(url, payload, max_retries=1)
            print(f"✓ Typing indicator sent for {message_id[:20]}... Response: {result}")
            return result
        except Exception as e:
            print(f"⚠️ Typing indicator failed for {message_id[:20]}...: {e}")
            return {}

    async def send_button(
        self,
        to: str,
        body_text: str,
        buttons: list[dict[str, str]],
        header: str = "",
        footer: str = "",
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send an interactive button message.

        Args:
            to: Recipient phone number
            body_text: Main message text
            buttons: List of button dicts with 'id' and 'title' keys (max 3)
            header: Optional header text
            footer: Optional footer text
            message_id: If provided, send typing indicator. If None, auto-fetch from Redis.

        Returns:
            API response from WhatsApp
        """
        url = self._get_url()

        # Send typing indicator before button message
        message_id = await self._ensure_message_id(to, message_id)
        if message_id:
            await self.send_typing_indicator(message_id)

        # Build button rows (max 3 buttons)
        button_rows = [{"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}} for btn in buttons[:3]]

        interactive_payload: dict[str, Any] = {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": button_rows},
        }

        if header and header.strip():
            interactive_payload["header"] = {"type": "text", "text": header}
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
            print(f"✓ Button message sent to {to}")
            return result
        except Exception as e:
            print(f"❌ Failed to send button message: {e}")
            raise

    async def send_list(
        self,
        to: str,
        body_text: str,
        options: list[dict[str, str]],
        header: str = "",
        footer: str = "",
        list_button_text: str = "View options",
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an interactive list message (up to 10 options)."""
        if not options:
            raise ValueError("List options cannot be empty")

        url = self._get_url()
        message_id = await self._ensure_message_id(to, message_id)
        if message_id:
            await self.send_typing_indicator(message_id)

        rows: list[dict[str, str]] = []
        for idx, option in enumerate(options[:10], start=1):
            raw_id = str(option.get("id", "")).strip() or str(idx)
            raw_title = str(option.get("title", f"Option {idx}")).strip() or f"Option {idx}"
            row: dict[str, str] = {"id": raw_id, "title": raw_title[:24]}
            description = str(option.get("description", "")).strip()
            if description:
                row["description"] = description[:72]
            rows.append(row)

        interactive_payload: dict[str, Any] = {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": list_button_text[:20],
                "sections": [{"title": "Options", "rows": rows}],
            },
        }

        if header and header.strip():
            interactive_payload["header"] = {"type": "text", "text": header}
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
            print(f"✓ List message sent to {to}")
            return result
        except Exception as e:
            print(f"❌ Failed to send list message: {e}")
            raise

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_config: dict[str, Any],
        message_id: str | None = None,
    ) -> MessageResult:
        """Send a WhatsApp flow.

        Args:
            to: Recipient phone number
            flow_id: WhatsApp Flow ID
            flow_config: Configuration dictionary containing:
                - flow_cta (str): Call-to-action button text
                - screen_name (str): Initial screen name to show
                - header (str): Flow header text
                - text_body (str): Flow body text
                - footer (str, optional): Footer text
                - flow_token (str, optional): Flow token for state management
                - flow_action_payload (dict, optional): Custom flow action payload
            message_id: If provided, send typing indicator. If None, auto-fetch from Redis.

        Returns:
            MessageResult with success status and raw response
        """
        url = self._get_url()

        message_id = await self._ensure_message_id(to, message_id)
        if message_id:
            await self.send_typing_indicator(message_id)

        # Extract config
        header = flow_config.get("header", "")
        text_body = flow_config.get("text_body", "")
        flow_cta = flow_config.get("flow_cta", "Start")
        screen_name = flow_config.get("screen_name", "")
        footer = flow_config.get("footer", "")
        flow_token = flow_config.get("flow_token", "")
        flow_action_payload = flow_config.get("flow_action_payload")

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
                    "flow_action_payload": (flow_action_payload if flow_action_payload else {"screen": screen_name}),
                },
            },
        }

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
            msg_id = result.get("messages", [{}])[0].get("id")
            return MessageResult(success=True, message_id=msg_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send flow: {e}")
            print(f"   Payload was: {json.dumps(payload, indent=2)}")
            # Raise exception if it's critical, or return failed result?
            # Existing clients might expect raise, but interface says return result.
            # However, for now let's return failed result to inhibit crash
            return MessageResult(success=False, error=str(e))

    async def _upload_media_to_whatsapp(self, media_url: str) -> str:
        """
        Upload media to WhatsApp and get media ID.

        Args:
            media_url: Public URL of the media file

        Returns:
            Media ID from WhatsApp
        """
        upload_url = f"{GRAPH_API_BASE}/{self.phone_number_id}/media"
        headers = self._get_headers()

        payload = {
            "messaging_product": "whatsapp",
            "url": media_url,
            "type": "image",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(upload_url, headers=headers, json=payload)
                resp.raise_for_status()
                result = resp.json()
                media_id: str | None = result.get("id")
                if not media_id:
                    raise ValueError("No media ID returned from WhatsApp")
                print(f"✓ Media uploaded to WhatsApp: {media_id}")
                return media_id
        except Exception as e:
            print(f"❌ Failed to upload media to WhatsApp: {e}")
            raise

    async def send_image(self, to: str, image_url: str, caption: str = "") -> dict[str, Any]:
        """
        Send an image to a WhatsApp number.

        Args:
            to: Recipient phone number
            image_url: Public URL of the image (must be accessible by WhatsApp)
            caption: Optional caption text

        Returns:
            API response from WhatsApp
        """
        try:
            media_id = await self._upload_media_to_whatsapp(image_url)
            url = self._get_url()
            image_payload: dict[str, Any] = {
                "id": media_id,
            }
            if caption:
                image_payload["caption"] = caption

            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "image",
                "image": image_payload,
            }

            result = await self._send(url, payload)
            print(f"✓ Image message sent to {to}")
            return result
        except Exception as e:
            print(f"❌ Failed to send image message: {e}")
            raise

    async def send_image_data(
        self,
        to: str,
        data: bytes,
        caption: str = "",
        mime_type: str = "image/png",
    ) -> dict[str, Any]:
        """
        Send an image from bytes data to a WhatsApp number.

        Args:
            to: Recipient phone number
            data: Image content as bytes (e.g., from PIL or generated images)
            caption: Optional caption text
            mime_type: MIME type of the image (default: image/png)

        Returns:
            API response from WhatsApp
        """
        try:
            # Generate a filename based on mime type
            extension = mime_type.split("/")[-1]
            filename = f"image.{extension}"

            media_id = await self._upload_buffer(data, filename, mime_type)

            url = self._get_url()
            image_payload: dict[str, Any] = {
                "id": media_id,
            }
            if caption:
                image_payload["caption"] = caption

            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "image",
                "image": image_payload,
            }

            result = await self._send(url, payload)
            print(f"✓ Image (from data) sent to {to}")
            return result
        except Exception as e:
            print(f"❌ Failed to send image from data: {e}")
            raise

    async def get_media_url(self, media_id: str) -> str:
        """
        Get the download URL for a media ID.

        Args:
            media_id: Media ID from Meta

        Returns:
            Publicly accessible URL (with auth token appended) or internal URL
        """
        url = f"{GRAPH_API_BASE}/{media_id}"
        headers = self._get_headers()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                result = resp.json()
                media_url: str | None = result.get("url")
                if not media_url:
                    raise ValueError("No URL returned for media")
                return media_url
        except Exception as e:
            print(f"❌ Failed to get media URL: {e}")
            raise

    async def download_media(self, media_url: str) -> bytes:
        """
        Download media binary content.

        Args:
            media_url: URL obtained from get_media_url

        Returns:
            Binary content
        """
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(media_url, headers=headers)
                resp.raise_for_status()
                return resp.content
        except Exception as e:
            print(f"❌ Failed to download media: {e}")
            raise

    async def _upload_buffer(
        self,
        data: bytes,
        filename: str,
        mime_type: str = "application/pdf",
    ) -> str:
        """
        Upload a buffer/blob to WhatsApp and get media ID.

        Args:
            data: File content as bytes
            filename: Display filename for the upload
            mime_type: MIME type of the file (default: application/pdf)

        Returns:
            Media ID from WhatsApp
        """
        upload_url = f"{GRAPH_API_BASE}/{self.phone_number_id}/media"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                files: dict[str, tuple[str | None, bytes | str, str] | tuple[str | None, str]] = {
                    "file": (filename, data, mime_type),
                    "messaging_product": (None, "whatsapp"),
                    "type": (None, mime_type),
                }
                headers = {"Authorization": f"Bearer {self.access_token}"}

                resp = await client.post(upload_url, headers=headers, files=files)
                resp.raise_for_status()
                result = resp.json()
                media_id: str | None = result.get("id")
                if not media_id:
                    raise ValueError("No media ID returned from WhatsApp")
                print(f"✓ Buffer uploaded to WhatsApp: {media_id}")
                return media_id
        except Exception as e:
            print(f"❌ Failed to upload buffer to WhatsApp: {e}")
            raise

    async def send_document(
        self,
        to: str,
        data: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "application/pdf",
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a document (PDF, etc.) to a WhatsApp number.

        Args:
            to: Recipient phone number
            data: Document content as bytes
            filename: Display filename for the document
            caption: Optional caption text
            mime_type: MIME type (default: application/pdf)
            message_id: If provided, send typing indicator. If None, auto-fetch from Redis.

        Returns:
            API response from WhatsApp
        """
        message_id = await self._ensure_message_id(to, message_id)
        if message_id:
            await self.send_typing_indicator(message_id)

        try:
            media_id = await self._upload_buffer(data, filename, mime_type)

            url = self._get_url()
            document_payload: dict[str, Any] = {
                "id": media_id,
                "filename": filename,
            }

            if caption:
                document_payload["caption"] = caption

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "document",
                "document": document_payload,
            }

            result = await self._send(url, payload)
            print(f"✓ Document sent to {to}: {filename}")
            return result
        except Exception as e:
            print(f"❌ Failed to send document: {e}")
            raise

    # ========== MessagingClient Interface Methods ==========
    # These implement the abstract interface for channel independence

    async def send_interactive(
        self,
        to: str,
        body_text: str,
        options: list[dict[str, str]],
        header: str = "",
        footer: str = "",
        message_id: str | None = None,
    ) -> MessageResult:
        """Implement MessagingClient.send_interactive using WhatsApp native buttons/lists."""
        try:
            if len(options) <= 3:
                result = await self.send_button(
                    to=to,
                    body_text=body_text,
                    buttons=options,
                    header=header,
                    footer=footer,
                    message_id=message_id,
                )
            elif len(options) <= 10:
                result = await self.send_list(
                    to=to,
                    body_text=body_text,
                    options=options,
                    header=header,
                    footer=footer,
                    message_id=message_id,
                )
            else:
                raise ValueError("WhatsApp interactive supports at most 10 options")
            msg_id = result.get("messages", [{}])[0].get("id")
            return MessageResult(success=True, message_id=msg_id, raw_response=result)
        except Exception as e:
            return MessageResult(success=False, error=str(e))
