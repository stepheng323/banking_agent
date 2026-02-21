"""Media service for handling voice and image messages."""

import base64
import io

from openai import AsyncOpenAI

from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MediaService:
    """Service for handling media messages (audio, images)."""

    def __init__(self, whatsapp_client: WhatsAppClient):
        """
        Initialize media service.

        Args:
            whatsapp_client: Client for downloading media from WhatsApp
        """
        self.whatsapp_client = whatsapp_client
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def process_audio(self, media_id: str, locale: str = "en") -> str:
        """
        Download and transcribe audio message.

        Args:
            media_id: WhatsApp media ID
            locale: User locale for deterministic fallback messaging

        Returns:
            Transcribed text
        """
        try:
            media_url = await self.whatsapp_client.get_media_url(media_id)
            audio_content = await self.whatsapp_client.download_media(media_url)

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

    async def get_image_data(self, media_id: str) -> str | None:
        """
        Get image data as base64 string for LLM consumption.

        Args:
            media_id: WhatsApp media ID

        Returns:
            Base64 encoded image string or None if failed
        """
        try:
            media_url = await self.whatsapp_client.get_media_url(media_id)
            image_content = await self.whatsapp_client.download_media(media_url)

            base64_image = base64.b64encode(image_content).decode("utf-8")
            return f"data:image/jpeg;base64,{base64_image}"

        except Exception:
            logger.error("failed_to_get_image")
            return None
