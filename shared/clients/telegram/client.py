"""Telegram client for sending messages via the Telegram Bot API."""

import asyncio
from typing import Any

import httpx

import shared.clients.telegram.control as telegram_control
import shared.clients.telegram.media as telegram_media
import shared.clients.telegram.media_messages as telegram_media_messages
import shared.clients.telegram.messages as telegram_messages
import shared.clients.telegram.mini_app as telegram_mini_app
from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

TELEGRAM_API_BASE = "https://api.telegram.org"
logger = get_logger(__name__)


class TelegramClient(MessagingClient):
    """Telegram Bot API client implementing the MessagingClient interface."""

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def supports_flows(self) -> bool:
        return False

    def __init__(self) -> None:
        self.bot_token = settings.telegram_bot_token
        self.mini_app_base_url = settings.telegram_mini_app_base_url
        self._http_client: httpx.AsyncClient | None = None
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate Telegram client configuration."""
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    def _api_url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.bot_token}/{method}"

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=30,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Make a Telegram Bot API call with retries."""
        url = self._api_url(method)
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                client = self._client()
                if files:
                    # multipart upload (photos / documents from bytes)
                    data: dict[str, Any] = payload or {}
                    resp = await client.post(url, data=data, files=files)
                else:
                    resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()

                if not result.get("ok"):
                    desc = result.get("description", "Unknown error")
                    raise ValueError(f"Telegram API error: {desc}")

                logger.debug("telegram_api_call_success", method=method, attempt=attempt)
                return result

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                if status == 401:
                    logger.error("telegram_api_auth_failed", method=method, http_status=status)
                    raise
                if attempt < max_retries:
                    logger.warning(
                        "telegram_api_http_retry",
                        method=method,
                        http_status=status,
                        attempt=attempt,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error("telegram_api_http_failed", method=method, http_status=status)
                    raise

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        "telegram_api_connect_retry",
                        method=method,
                        attempt=attempt,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(2 * attempt)
                else:
                    logger.error("telegram_api_connect_failed", method=method, error_type=type(e).__name__)
                    raise

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        "telegram_api_call_retry",
                        method=method,
                        attempt=attempt,
                        max_retries=max_retries,
                        error_type=type(e).__name__,
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error("telegram_api_call_failed", method=method, error_type=type(e).__name__)
                    raise

        if last_error:
            raise last_error
        return {}

    async def send_text(
        self,
        to: str,
        text: str,
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """Send a plain text message via Telegram."""
        return await telegram_messages.send_text(
            api_call=self._call,
            to=to,
            text=text,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

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
        """Send an interactive message with inline keyboard buttons."""
        return await telegram_messages.send_interactive(
            api_call=self._call,
            to=to,
            body_text=body_text,
            options=options,
            header=header,
            footer=footer,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = "",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """Send an image by URL."""
        return await telegram_media_messages.send_image_from_url(
            api_call=self._call,
            to=to,
            image_url=image_url,
            caption=caption,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

    async def send_image_data(
        self,
        to: str,
        data: bytes,
        caption: str = "",
        mime_type: str = "image/png",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """Send an image from bytes via multipart upload."""
        return await telegram_media_messages.send_image_data(
            api_call=self._call,
            to=to,
            data=data,
            caption=caption,
            mime_type=mime_type,
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

    async def send_typing_indicator(self, chat_id: str) -> bool:
        """Send typing indicator (chat action)."""
        return await telegram_control.send_typing_indicator(api_call=self._call, chat_id=chat_id)

    async def send_document(
        self,
        to: str,
        data: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "application/pdf",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send a document via multipart upload."""
        return await telegram_media_messages.send_document(
            api_call=self._call,
            to=to,
            data=data,
            filename=filename,
            caption=caption,
            mime_type=mime_type,
            message_id=message_id,
        )

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_config: dict[str, Any],
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """Send a flow via Telegram Mini App.

        Opens a Mini App (web_app button) for secure data entry.
        Falls back to text if no Mini App base URL is configured.
        """
        del flow_id
        return await self.send_mini_app(
            to=to,
            flow_token=flow_config.get("flow_token", ""),
            header=flow_config.get("header", ""),
            body_text=flow_config.get("text_body", ""),
            cta_text=flow_config.get("flow_cta", "Open"),
            message_id=message_id,
            suppress_typing_indicator=suppress_typing_indicator,
        )

    async def send_mini_app(
        self,
        to: str,
        flow_token: str,
        header: str = "",
        body_text: str = "",
        cta_text: str = "Open",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """Send an inline keyboard button that opens a Telegram Mini App.

        Used for secure PIN entry and other flow-like interactions.
        """
        if not self.mini_app_base_url:
            # No Mini App configured — send a text fallback
            return await self.send_text(
                to=to,
                text=body_text or "This action requires a Mini App. Please contact support.",
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
            )

        return await telegram_mini_app.send_mini_app_message(
            api_call=self._call,
            mini_app_base_url=self.mini_app_base_url,
            to=to,
            flow_token=flow_token,
            header=header,
            body_text=body_text,
            cta_text=cta_text,
        )

    async def get_media_url(self, media_id: str) -> str:
        """Get the URL for a Telegram file."""
        return await telegram_media.get_media_url(api_call=self._call, bot_token=self.bot_token, media_id=media_id)

    async def download_media(self, media_url: str) -> bytes:
        """Download media bytes from Telegram."""
        return await telegram_media.download_media(http_client=self._client(), media_url=media_url)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
    ) -> bool:
        """Answer a callback query (acknowledge inline button press)."""
        return await telegram_control.answer_callback_query(
            api_call=self._call,
            callback_query_id=callback_query_id,
            text=text,
        )

    async def mark_as_authorized(self, chat_id: str, message_id: str | int) -> bool:
        """Replace the PIN Web App button with a non-interactive 'Authorized' badge.

        Called after a successful PIN auth so the user sees confirmation but
        cannot re-open the Mini App.
        """
        return await telegram_control.mark_as_authorized(api_call=self._call, chat_id=chat_id, message_id=message_id)

    async def remove_inline_keyboard(self, chat_id: str, message_id: str | int) -> bool:
        """Remove an inline keyboard from a sent Telegram message."""
        return await telegram_control.remove_inline_keyboard(
            api_call=self._call,
            chat_id=chat_id,
            message_id=message_id,
        )

    async def set_webhook(self, webhook_url: str) -> bool:
        """Register the webhook URL with Telegram."""
        return await telegram_control.set_webhook(api_call=self._call, webhook_url=webhook_url)

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        """Register the persistent bot menu commands.
        commands should be a list like: [{"command": "start", "description": "Start the bot"}]
        """
        return await telegram_control.set_my_commands(api_call=self._call, commands=commands)
