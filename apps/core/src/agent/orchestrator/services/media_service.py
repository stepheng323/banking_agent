"""Media service for handling voice and image messages."""
import base64
import io
from typing import Optional

from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from shared.clients.whatsapp_client import WhatsAppClient
from shared.config import settings


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

    async def process_audio(self, media_id: str) -> str:
        """
        Download and transcribe audio message.

        Args:
            media_id: WhatsApp media ID

        Returns:
            Transcribed text
        """
        try:
            media_url = await self.whatsapp_client.get_media_url(media_id)
            audio_content = await self.whatsapp_client.download_media(media_url)

            buffer = io.BytesIO(audio_content)
            buffer.name = "voice_note.ogg"

            transcription = await self.openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=buffer,
                response_format="text"
            )

            text = str(transcription).strip()
            print(f"🎤 Transcribed audio: {text}")
            return text

        except Exception as e:
            print(f"❌ Failed to process audio: {e}")
            return "Attributes of the audio could not be processed."

    async def get_image_data(self, media_id: str) -> Optional[str]:
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

            base64_image = base64.b64encode(image_content).decode('utf-8')
            return f"data:image/jpeg;base64,{base64_image}"

        except Exception as e:
            print(f"❌ Failed to get image data: {e}")
            return None
