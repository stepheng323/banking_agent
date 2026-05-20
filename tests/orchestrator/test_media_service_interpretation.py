import json
from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.services.media_service import MediaService
from shared.config.settings import settings


class _MessagingClientStub:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.media_urls: list[str] = []

    async def get_media_url(self, media_id: str) -> str:
        self.media_urls.append(media_id)
        return f"https://media.test/{media_id}"

    async def download_media(self, media_url: str) -> bytes:
        assert media_url.startswith("https://media.test/")
        return self.content


class _VisionResponsesStub:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.payload))


class _AudioTranscriptionsStub:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.response


def _service(client: _MessagingClientStub) -> MediaService:
    return MediaService({"telegram": client, "whatsapp": client})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_interpret_image_builds_mime_aware_data_url_and_renders_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_image_model", "vision-test")
    monkeypatch.setattr(settings, "media_image_max_bytes", 1000)
    media_client = _MessagingClientStub(b"image-bytes")
    service = _service(media_client)
    responses = _VisionResponsesStub(
        {
            "entities": {
                "recipient_account": "816 251 1023",
                "bank_name": "OPay",
                "recipient_name": "Tolu A.",
                "amount": "5000",
                "narration": "groceries",
                "visible_text": "Transaction Receipt",
            },
            "confidence": 0.91,
        }
    )
    service.openai_client = SimpleNamespace(responses=responses)

    interpretation = await service.interpret_image("file-1", channel="telegram", mime_type="image/png")

    assert interpretation.error is None
    assert interpretation.confidence == 0.91
    assert interpretation.entities == {
        "recipient_account": "8162511023",
        "bank_name": "OPay",
        "recipient_name": "Tolu A.",
        "amount": 5000.0,
        "narration": "groceries",
        "visible_text": "Transaction Receipt",
    }
    assert interpretation.text == (
        "Extracted from image: recipient_account=8162511023; bank_name=OPay; "
        "recipient_name=Tolu A.; amount=5000.0; narration=groceries."
    )
    assert "Transaction Receipt" not in interpretation.text
    call = responses.calls[0]
    assert call["model"] == "vision-test"
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["input"][1]["content"][1]["type"] == "input_image"
    image_url = call["input"][1]["content"][1]["image_url"]
    assert image_url.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_interpret_image_returns_error_for_oversized_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "media_image_max_bytes", 3)
    service = _service(_MessagingClientStub(b"large"))
    responses = _VisionResponsesStub({"entities": {}, "confidence": 0})
    service.openai_client = SimpleNamespace(responses=responses)

    interpretation = await service.interpret_image("file-1", channel="whatsapp", mime_type="image/jpeg")

    assert interpretation.error == "image_too_large"
    assert interpretation.text == ""
    assert responses.calls == []


@pytest.mark.asyncio
async def test_process_audio_uses_configured_transcription_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_model", "audio-test")
    service = _service(_MessagingClientStub(b"audio-bytes"))
    transcriptions = _AudioTranscriptionsStub("send 5k to Tolu")
    service.openai_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

    text = await service.process_audio("voice-1", channel="telegram", locale="en")

    assert text == "send 5k to Tolu"
    assert transcriptions.calls[0]["model"] == "audio-test"
    assert transcriptions.calls[0]["file"].name == "voice_note.ogg"
