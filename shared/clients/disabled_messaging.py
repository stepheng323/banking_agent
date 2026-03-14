"""Disabled messaging client used when outbound sends are explicitly gated off."""

from typing import Any

from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DisabledMessagingClient(MessagingClient):
    """Messaging client that acknowledges sends without contacting external providers."""

    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name

    @property
    def channel_name(self) -> str:
        return self._channel_name

    async def send_text(
        self,
        to: str,
        text: str,
        message_id: str | None = None,
    ) -> MessageResult:
        return self._skip("send_text", to=to, message_id=message_id, text=text)

    async def send_interactive(
        self,
        to: str,
        body_text: str,
        options: list[dict[str, str]],
        header: str = "",
        footer: str = "",
        message_id: str | None = None,
    ) -> MessageResult:
        return self._skip(
            "send_interactive",
            to=to,
            message_id=message_id,
            body_text=body_text,
            header=header,
            footer=footer,
            option_count=len(options),
        )

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = "",
        message_id: str | None = None,
    ) -> MessageResult:
        return self._skip("send_image", to=to, image_url=image_url, caption=caption, message_id=message_id)

    async def send_image_data(
        self,
        to: str,
        data: bytes,
        caption: str = "",
        mime_type: str = "image/png",
        message_id: str | None = None,
    ) -> MessageResult:
        return self._skip(
            "send_image_data",
            to=to,
            size=len(data),
            caption=caption,
            mime_type=mime_type,
            message_id=message_id,
        )

    async def get_media_url(self, media_id: str) -> str:
        raise RuntimeError(f"media_fetch_disabled channel={self.channel_name} media_id={media_id}")

    async def download_media(self, media_url: str) -> bytes:
        raise RuntimeError(f"media_download_disabled channel={self.channel_name} media_url={media_url}")

    def _skip(self, action: str, **payload: Any) -> MessageResult:
        logger.info(
            "outbound_send_skipped",
            channel=self.channel_name,
            action=action,
            payload=payload,
        )
        return MessageResult(success=True, message_id=f"disabled:{self.channel_name}")
