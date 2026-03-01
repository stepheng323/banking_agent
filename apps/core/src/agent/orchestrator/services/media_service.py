"""Media service for handling voice and image messages."""

import base64
import io

from openai import AsyncOpenAI

from shared.clients.abstractions.messaging import MessagingClient
from shared.config.settings import settings
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MediaService:
    """Service for handling media messages (audio, images)."""

    def __init__(self, messaging_clients: dict[str, MessagingClient]):
        """
        Initialize media service.

        Args:
            messaging_clients: Dictionary mapping channel name to its MessagingClient
        """
        self.messaging_clients = messaging_clients
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _get_client(self, channel: str) -> MessagingClient:
        """Get the messaging client for the requested channel."""
        client = self.messaging_clients.get(channel)
        if not client:
            logger.warning("media_service_channel_fallback", requested=channel)
            client = self.messaging_clients.get("whatsapp")  # default fallback
            if not client:
                client = next(iter(self.messaging_clients.values()))
        return client

    async def process_audio(self, media_id: str, channel: str = "whatsapp", locale: str = "en") -> str:
        """
        Download and transcribe audio message.

        Args:
            media_id: Media ID from the channel
            channel: Channel name (whatsapp, telegram)
            locale: User locale for deterministic fallback messaging

        Returns:
            Transcribed text
        """
        try:
            client = self._get_client(channel)
            media_url = await client.get_media_url(media_id)
            audio_content = await client.download_media(media_url)

            buffer = io.BytesIO(audio_content)
            buffer.name = "voice_note.ogg"

            transcription = await self.openai_client.audio.transcriptions.create(
                model="whisper-1", file=buffer, response_format="text"
            )

            text = str(transcription).strip()
            logger.info("transcribed")
            return text

        except Exception:
            logger.error("failed_to_process")
            return render_message("orchestrator.error.audio_unprocessable", locale)

    async def get_image_data(self, media_id: str, channel: str = "whatsapp") -> str | None:
        """
        Get image data as base64 string for LLM consumption.

        Args:
            media_id: Media ID from the channel
            channel: Channel name (whatsapp, telegram)

        Returns:
            Base64 encoded image string or None if failed
        """
        try:
            client = self._get_client(channel)
            media_url = await client.get_media_url(media_id)
            image_content = await client.download_media(media_url)

            base64_image = base64.b64encode(image_content).decode("utf-8")
            return f"data:image/jpeg;base64,{base64_image}"

        except Exception:
            logger.error("failed_to_get_image")
            return None
