"""WhatsApp client for sending messages and flows."""

import asyncio
from typing import Any

import httpx

import shared.clients.whatsapp.flows as whatsapp_flows
import shared.clients.whatsapp.media as whatsapp_media
import shared.clients.whatsapp.media_messages as whatsapp_media_messages
import shared.clients.whatsapp.messages as whatsapp_messages
from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.clients.whatsapp.endpoints import message_url
from shared.clients.whatsapp.payloads import build_typing_indicator_payload
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

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
                logger.debug("whatsapp_api_request_success", attempt=attempt)
                return result

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 401:
                    logger.error("whatsapp_api_auth_failed", http_status=401)
                    raise
                elif attempt < max_retries:
                    status = e.response.status_code
                    logger.warning(
                        "whatsapp_api_http_retry",
                        http_status=status,
                        attempt=attempt,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error("whatsapp_api_http_failed", http_status=e.response.status_code)
                    raise

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning("whatsapp_api_connect_retry", attempt=attempt, max_retries=max_retries)
                    await asyncio.sleep(2 * attempt)
                else:
                    logger.error("whatsapp_api_connect_failed", error_type=type(e).__name__)
                    raise
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        "whatsapp_api_request_retry",
                        attempt=attempt,
                        max_retries=max_retries,
                        error_type=type(e).__name__,
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error("whatsapp_api_request_failed", error_type=type(e).__name__)
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
            logger.warning("whatsapp_development_access_token_configured")

        if not self.phone_number_id:
            errors.append("META_PHONE_NUMBER_ID is not set")
        elif self.phone_number_id == "development_phone_id":
            logger.warning("whatsapp_development_phone_number_id_configured")

        if errors:
            error_msg = "WhatsApp client configuration errors:\n" + "\n".join(f"  - {error}" for error in errors)
            raise ValueError(error_msg)

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get_url(self) -> str:
        return message_url(self.phone_number_id)

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
        return await whatsapp_messages.send_text(
            url=self._get_url(),
            to=to,
            text=text,
            preview_url=preview_url,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
            send=self._send,
            send_typing_indicator=self.send_typing_indicator,
        )

    async def send_typing_indicator(self, message_id: str) -> dict[str, Any]:
        """Send a typing indicator to a WhatsApp number.

        Can be called multiple times for the same message_id - each call resets the ~5s timer.
        """
        url = self._get_url()

        payload = build_typing_indicator_payload(message_id=message_id)

        try:
            result = await self._send(url, payload, max_retries=1)
            logger.debug("whatsapp_typing_indicator_sent", message_id_hash=log_fingerprint(message_id))
            return result
        except Exception as e:
            logger.warning(
                "whatsapp_typing_indicator_failed",
                message_id_hash=log_fingerprint(message_id),
                error_type=type(e).__name__,
            )
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
        return await whatsapp_messages.send_button(
            url=self._get_url(),
            to=to,
            body_text=body_text,
            buttons=buttons,
            header=header,
            footer=footer,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
            send=self._send,
            send_typing_indicator=self.send_typing_indicator,
        )

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
        return await whatsapp_messages.send_list(
            url=self._get_url(),
            to=to,
            body_text=body_text,
            options=options,
            header=header,
            footer=footer,
            list_button_text=list_button_text,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
            send=self._send,
            send_typing_indicator=self.send_typing_indicator,
        )

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
        return await whatsapp_flows.send_flow(
            url=self._get_url(),
            to=to,
            flow_id=flow_id,
            flow_config=flow_config,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
            send=self._send,
            send_typing_indicator=self.send_typing_indicator,
        )

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
            return await whatsapp_media_messages.send_image_from_url(
                to=to,
                image_url=image_url,
                caption=caption,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
                http_client=self._client(),
                phone_number_id=self.phone_number_id,
                headers=self._get_headers(),
                send=self._send,
                send_typing_indicator=self.send_typing_indicator,
            )
        except Exception as e:
            logger.error("whatsapp_image_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
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
            return await whatsapp_media_messages.send_image_data(
                to=to,
                data=data,
                caption=caption,
                mime_type=mime_type,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
                http_client=self._client(),
                access_token=self.access_token,
                phone_number_id=self.phone_number_id,
                send=self._send,
                send_typing_indicator=self.send_typing_indicator,
            )
        except Exception as e:
            logger.error("whatsapp_image_data_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
            raise

    async def get_media_url(self, media_id: str) -> str:
        """
        Get the download URL for a media ID.

        Args:
            media_id: Media ID from Meta

        Returns:
            Publicly accessible URL (with auth token appended) or internal URL
        """
        return await whatsapp_media.get_media_url(
            http_client=self._client(),
            headers=self._get_headers(),
            media_id=media_id,
        )

    async def download_media(self, media_url: str) -> bytes:
        """
        Download media binary content.

        Args:
            media_url: URL obtained from get_media_url

        Returns:
            Binary content
        """
        return await whatsapp_media.download_media(
            http_client=self._client(),
            headers=self._get_headers(),
            media_url=media_url,
        )

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
        try:
            return await whatsapp_media_messages.send_document(
                to=to,
                data=data,
                filename=filename,
                caption=caption,
                mime_type=mime_type,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
                http_client=self._client(),
                access_token=self.access_token,
                phone_number_id=self.phone_number_id,
                send=self._send,
                send_typing_indicator=self.send_typing_indicator,
            )
        except Exception as e:
            logger.error("whatsapp_document_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
            raise

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
        return await whatsapp_messages.send_interactive(
            url=self._get_url(),
            to=to,
            body_text=body_text,
            options=options,
            header=header,
            footer=footer,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
            send=self._send,
            send_typing_indicator=self.send_typing_indicator,
        )
