"""Abstract messaging client interface for channel independence.

This module provides the MessagingClient abstract base class that allows
the core banking agent to work with any messaging platform (WhatsApp,
Telegram, SMS, web chat, etc.) without code changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class MessageResult:
    """Result of a message send operation."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    raw_response: dict[str, Any] | None = None


class MessagingClient(ABC):
    """
    Abstract base class for messaging platform clients.

    Implementations:
        - WhatsAppClient: Meta WhatsApp Business API
        - TelegramClient: Telegram Bot API (future)
        - SMSClient: Twilio/Africa's Talking (future)
        - WebChatClient: WebSocket-based (future)
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Return the name of this channel (e.g., 'whatsapp', 'telegram')."""
        ...

    @property
    def supports_flows(self) -> bool:
        """Return proper whether channel supports flows/forms."""
        return False

    @abstractmethod
    async def send_text(
        self,
        to: str,
        text: str,
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """
        Send a plain text message.

        Args:
            to: Recipient identifier (phone number, user ID, etc.)
            text: Message content
            message_id: Optional reference message ID (for reply context)
            suppress_typing_indicator: Skip channel typing UX before sending when True

        Returns:
            MessageResult with success status and message ID
        """
        ...

    @abstractmethod
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
        """
        Send an interactive message with options.

        For WhatsApp: buttons or list
        For Telegram: inline keyboard
        For SMS: numbered options in text
        For Voice: spoken options

        Args:
            to: Recipient identifier
            body_text: Main message text
            options: List of option dicts with 'id' and 'title' keys
            header: Optional header text
            footer: Optional footer text
            message_id: Optional reference message ID
            suppress_typing_indicator: Skip channel typing UX before sending when True

        Returns:
            MessageResult with success status
        """
        ...

    @abstractmethod
    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = "",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """
        Send an image with optional caption.

        Args:
            to: Recipient identifier
            image_url: URL of the image to send
            caption: Optional caption text
            message_id: Optional reference message ID
            suppress_typing_indicator: Skip channel typing UX before sending when True

        Returns:
            MessageResult with success status
        """
        ...

    async def send_image_data(
        self,
        to: str,
        data: bytes,
        caption: str = "",
        mime_type: str = "image/png",
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """
        Send an image from bytes data.

        Default implementation returns not supported.
        Override in channel-specific implementations.
        """
        return MessageResult(
            success=False,
            error=f"send_image_data not supported on {self.channel_name}",
        )

    async def send_typing_indicator(self, message_id: str) -> bool:
        """
        Send a typing indicator.

        Default implementation does nothing (not all channels support this).
        """
        return True

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_config: dict[str, Any],
        message_id: str | None = None,
        suppress_typing_indicator: bool = False,
    ) -> MessageResult:
        """
        Send a structured flow/form.

        Default implementation returns not supported.
        WhatsApp has native flows, other channels may need alternatives.
        """
        return MessageResult(
            success=False,
            error=f"send_flow not supported on {self.channel_name}",
        )

    @abstractmethod
    async def get_media_url(self, media_id: str) -> str:
        """
        Get the download URL for a piece of media identified by media_id.
        """
        ...

    @abstractmethod
    async def download_media(self, media_url: str) -> bytes:
        """
        Download the actual bytes of media from the given URL.
        """
        ...
