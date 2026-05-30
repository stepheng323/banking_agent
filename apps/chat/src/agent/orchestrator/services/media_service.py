"""Media service for handling voice and image messages."""

import base64
import io
import json
import re
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.messaging import MessagingClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_IMAGE_ENTITY_FIELDS = (
    "recipient_account",
    "bank_name",
    "recipient_name",
    "amount",
    "narration",
    "visible_text",
)
_IMAGE_TEXT_FIELDS = tuple(field for field in _IMAGE_ENTITY_FIELDS if field != "visible_text")

_IMAGE_INTERPRETATION_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "object",
            "properties": {
                "recipient_account": {"type": ["string", "null"]},
                "bank_name": {"type": ["string", "null"]},
                "recipient_name": {"type": ["string", "null"]},
                "amount": {"type": ["string", "null"]},
                "narration": {"type": ["string", "null"]},
                "visible_text": {"type": ["string", "null"]},
            },
            "required": list(_IMAGE_ENTITY_FIELDS),
            "additionalProperties": False,
        },
        "confidence": {"type": "number"},
    },
    "required": ["entities", "confidence"],
    "additionalProperties": False,
}


class MediaInterpretation(BaseModel):
    """Text-first interpretation of inbound media."""

    source: Literal["audio", "image"]
    text: str = ""
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    error: str | None = None


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
                model=settings.audio_transcription_model, file=buffer, response_format="text"
            )

            text = str(transcription).strip()
            logger.info("transcribed")
            return text

        except Exception:
            logger.error("failed_to_process")
            return render_message("orchestrator.error.audio_unprocessable", locale)

    @staticmethod
    def _normalize_mime_type(mime_type: str | None) -> str:
        if not mime_type:
            return "image/jpeg"
        clean = mime_type.split(";", 1)[0].strip().lower()
        if clean.startswith("image/"):
            return clean
        return "image/jpeg"

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(confidence, 1.0))

    @staticmethod
    def _clean_text(value: Any, *, limit: int = 160) -> str | None:
        if value is None:
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        if not text:
            return None
        if len(text) > limit:
            return f"{text[: limit - 1].rstrip()}..."
        return text

    @classmethod
    def _coerce_image_entities(cls, raw_entities: Any) -> dict[str, Any]:
        if not isinstance(raw_entities, dict):
            return {}

        entities: dict[str, Any] = {}
        for field in _IMAGE_ENTITY_FIELDS:
            value = raw_entities.get(field)
            if value in (None, ""):
                continue

            if field == "recipient_account":
                digits = re.sub(r"\D+", "", str(value))
                if len(digits) == 10:
                    entities[field] = digits
                continue

            if field == "amount":
                try:
                    amount = float(str(value).replace(",", "").strip())
                except ValueError:
                    continue
                if amount > 0:
                    entities[field] = amount
                continue

            limit = 280 if field == "visible_text" else 120
            cleaned = cls._clean_text(value, limit=limit)
            if cleaned:
                entities[field] = cleaned

        return entities

    @staticmethod
    def render_image_interpretation_text(entities: dict[str, Any]) -> str:
        """Render media entities into deterministic text for downstream grounding."""
        parts: list[str] = []
        for field in _IMAGE_TEXT_FIELDS:
            value = entities.get(field)
            if value in (None, ""):
                continue
            parts.append(f"{field}={value}")
        if not parts:
            return ""
        return f"Extracted from image: {'; '.join(parts)}."

    @staticmethod
    def _response_content(response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            return output_text

        output = getattr(response, "output", None)
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                content = getattr(item, "content", None)
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    text = getattr(content_item, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "\n".join(parts)

        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return ""

    @staticmethod
    def _safe_error_detail(exc: Exception) -> str:
        message = getattr(exc, "message", None) or str(exc)
        return re.sub(r"\s+", " ", str(message)).strip()[:500]

    async def interpret_image(
        self,
        media_id: str,
        channel: str = "whatsapp",
        mime_type: str | None = None,
        locale: str = "en",
    ) -> MediaInterpretation:
        """Download an image and convert visible transfer details into compact text."""
        try:
            client = self._get_client(channel)
            media_url = await client.get_media_url(media_id)
            image_content = await client.download_media(media_url)

            max_bytes = max(1, int(settings.media_image_max_bytes))
            if len(image_content) > max_bytes:
                logger.warning("media_image_too_large", size_bytes=len(image_content), max_bytes=max_bytes)
                return MediaInterpretation(source="image", error="image_too_large")

            normalized_mime = self._normalize_mime_type(mime_type)
            base64_image = base64.b64encode(image_content).decode("utf-8")
            image_url = f"data:{normalized_mime};base64,{base64_image}"

            response = await self.openai_client.responses.create(
                model=settings.media_image_model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Extract Nigerian banking transfer details from the image. "
                            "Return JSON only with keys: entities, confidence. "
                            "entities may contain recipient_account, bank_name, recipient_name, "
                            "amount, narration, visible_text. Only include values visible in the image. "
                            "recipient_account must be exactly 10 digits when present."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Extract bank account details and transfer-relevant text from this image.",
                            },
                            {"type": "input_image", "image_url": image_url},
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "media_image_interpretation",
                        "schema": _IMAGE_INTERPRETATION_SCHEMA,
                        "strict": True,
                    }
                },
            )
            content = self._response_content(response)
            parsed = json.loads(content) if content else {}
            raw_entities = parsed.get("entities") if isinstance(parsed, dict) else {}
            entities = self._coerce_image_entities(raw_entities or parsed)
            text = self.render_image_interpretation_text(entities)
            return MediaInterpretation(
                source="image",
                text=text,
                entities=entities,
                confidence=self._coerce_confidence(parsed.get("confidence")),
            )

        except Exception as exc:
            logger.error(
                "failed_to_interpret_image",
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
                error_message=self._safe_error_detail(exc),
            )
            return MediaInterpretation(
                source="image",
                error=render_message("orchestrator.error.image_unprocessable", locale),
            )

    async def get_image_data(
        self,
        media_id: str,
        channel: str = "whatsapp",
        mime_type: str | None = None,
    ) -> str | None:
        """
        Get image data as base64 string for LLM consumption.

        Args:
            media_id: Media ID from the channel
            channel: Channel name (whatsapp, telegram)
            mime_type: MIME type from the channel, if available

        Returns:
            Base64 encoded image string or None if failed
        """
        try:
            client = self._get_client(channel)
            media_url = await client.get_media_url(media_id)
            image_content = await client.download_media(media_url)

            base64_image = base64.b64encode(image_content).decode("utf-8")
            return f"data:{self._normalize_mime_type(mime_type)};base64,{base64_image}"

        except Exception:
            logger.error("failed_to_get_image")
            return None
