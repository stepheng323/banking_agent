"""WhatsApp client for sending messages and flows."""

import asyncio
from typing import Any

import httpx

from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

GRAPH_API_BASE = "https://graph.facebook.com/v24.0"
logger = get_logger(__name__)


class WhatsAppClient(MessagingClient):
    """WhatsApp client for sending messages and flows."""

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def supports_flows(self) -> bool:
        return True

    def __init__(self):
        self.access_token = settings.whatsapp.access_token
        self.phone_number_id = settings.whatsapp.phone_number_id
        self._http_client: httpx.AsyncClient | None = None
        self._validate_config()

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=60,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _send(self, url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        headers = self._get_headers()
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client().post(url, headers=headers, json=payload, timeout=10)
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

    async def _maybe_send_typing_indicator(
        self,
        *,
        to: str,
        message_id: str | None,
        suppress_typing_indicator: bool,
    ) -> str | None:
        """Resolve the reference message and surface typing before a visible outbound send."""
        resolved_message_id = await self._ensure_message_id(to, message_id)
        if not resolved_message_id or suppress_typing_indicator:
            return resolved_message_id

        await self.send_typing_indicator(resolved_message_id)
        delay_seconds = max(0.0, settings.whatsapp.typing_indicator_delay_ms / 1000)
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        return resolved_message_id

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
        suppress_typing_indicator: bool = False,
    ) -> dict[str, Any]:
        """Send a text message to a WhatsApp number.

        Args:
            to: Recipient phone number
            text: Message content
            preview_url: Whether to show URL preview
            message_id: If provided, send typing indicator. If None, auto-fetch from Redis.
        """
        url = self._get_url()

        await self._maybe_send_typing_indicator(
            to=to,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )
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
        suppress_typing_indicator: bool = False,
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

        await self._maybe_send_typing_indicator(
            to=to,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

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
        suppress_typing_indicator: bool = False,
    ) -> dict[str, Any]:
        """Send an interactive list message (up to 10 options)."""
        if not options:
            raise ValueError("List options cannot be empty")

        url = self._get_url()
        await self._maybe_send_typing_indicator(
            to=to,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

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
        suppress_typing_indicator: bool = False,
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

        await self._maybe_send_typing_indicator(
            to=to,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

        # Extract config
        header = flow_config.get("header", "")
        text_body = flow_config.get("text_body", "")
        flow_cta = flow_config.get("flow_cta", "Start")
        screen_name = flow_config.get("screen_name", "")
        footer = flow_config.get("footer", "")
        flow_token = flow_config.get("flow_token", "")
        flow_action = flow_config.get("flow_action", "navigate")
        flow_action_payload = flow_config.get("flow_action_payload")
        parameters: dict[str, Any] = {
            "flow_message_version": "3",
            "flow_token": flow_token or "",
            "flow_id": flow_id,
            "flow_cta": flow_cta,
            "flow_action": flow_action,
        }
        if flow_action_payload is not None:
            parameters["flow_action_payload"] = flow_action_payload
        elif flow_action == "navigate":
            parameters["flow_action_payload"] = {"screen": screen_name}

        interactive_payload = {
            "type": "flow",
            "header": {"type": "text", "text": header},
            "body": {"text": text_body},
            "action": {
                "name": "flow",
                "parameters": parameters,
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

        action_payload = parameters.get("flow_action_payload")
        action_payload_data = action_payload.get("data") if isinstance(action_payload, dict) else None
        logger.info(
            "whatsapp_flow_send_prepared",
            to_hash=log_fingerprint(to),
            flow_id_hash=log_fingerprint(flow_id),
            flow_token_hash=log_fingerprint(flow_token),
            flow_action=flow_action,
            screen_name=screen_name,
            has_flow_action_payload="flow_action_payload" in parameters,
            flow_action_payload_keys=(
                sorted(str(key) for key in action_payload) if isinstance(action_payload, dict) else []
            ),
            flow_action_payload_data_keys=(
                sorted(str(key) for key in action_payload_data) if isinstance(action_payload_data, dict) else []
            ),
        )

        try:
            result = await self._send(url, payload)
            msg_id = result.get("messages", [{}])[0].get("id")
            logger.info(
                "whatsapp_flow_send_succeeded",
                to_hash=log_fingerprint(to),
                flow_id_hash=log_fingerprint(flow_id),
                flow_token_hash=log_fingerprint(flow_token),
                flow_action=flow_action,
                message_id=msg_id,
            )
            return MessageResult(success=True, message_id=msg_id, raw_response=result)
        except Exception as e:
            logger.error(
                "whatsapp_flow_send_failed",
                to_hash=log_fingerprint(to),
                flow_id_hash=log_fingerprint(flow_id),
                flow_token_hash=log_fingerprint(flow_token),
                flow_action=flow_action,
                error=str(e),
            )
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
            resp = await self._client().post(upload_url, headers=headers, json=payload, timeout=30)
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

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = "",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> dict[str, Any]:
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
            await self._maybe_send_typing_indicator(
                to=to,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
            )
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
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
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
            await self._maybe_send_typing_indicator(
                to=to,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
            )
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
            resp = await self._client().get(url, headers=headers, timeout=10)
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
            resp = await self._client().get(media_url, headers=headers, timeout=30)
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
            files: dict[str, tuple[str | None, bytes | str, str] | tuple[str | None, str]] = {
                "file": (filename, data, mime_type),
                "messaging_product": (None, "whatsapp"),
                "type": (None, mime_type),
            }
            headers = {"Authorization": f"Bearer {self.access_token}"}

            resp = await self._client().post(upload_url, headers=headers, files=files, timeout=60)
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
        suppress_typing_indicator: bool = False,
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
        await self._maybe_send_typing_indicator(
            to=to,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

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
        suppress_typing_indicator: bool = False,
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
                    suppress_typing_indicator=suppress_typing_indicator,
                )
            elif len(options) <= 10:
                result = await self.send_list(
                    to=to,
                    body_text=body_text,
                    options=options,
                    header=header,
                    footer=footer,
                    message_id=message_id,
                    suppress_typing_indicator=suppress_typing_indicator,
                )
            else:
                raise ValueError("WhatsApp interactive supports at most 10 options")
            msg_id = result.get("messages", [{}])[0].get("id")
            return MessageResult(success=True, message_id=msg_id, raw_response=result)
        except Exception as e:
            return MessageResult(success=False, error=str(e))
