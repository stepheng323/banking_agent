from typing import Any

import pytest

from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings


@pytest.mark.asyncio
async def test_whatsapp_client_send_text_suppresses_typing_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WhatsAppClient.__new__(WhatsAppClient)
    client.access_token = "token"
    client.phone_number_id = "phone-id"

    typing_calls: list[str] = []
    sleep_calls: list[float] = []

    async def _ensure_message_id(to: str, message_id: str | None) -> str | None:
        del to
        return message_id

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        return {"messages": [{"id": "wa-msg-1"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        typing_calls.append(message_id)
        return {}

    async def _sleep(delay_seconds: float) -> None:
        sleep_calls.append(delay_seconds)

    monkeypatch.setattr(client, "_ensure_message_id", _ensure_message_id)
    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr("shared.clients.whatsapp.client.asyncio.sleep", _sleep)
    monkeypatch.setattr(settings, "whatsapp_typing_indicator_delay_ms", 650)

    result = await client.send_text(
        to="2348000000000",
        text="Hello",
        message_id="wamid.123",
        suppress_typing_indicator=True,
    )

    assert result["messages"][0]["id"] == "wa-msg-1"
    assert typing_calls == []
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_whatsapp_client_send_text_keeps_typing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WhatsAppClient.__new__(WhatsAppClient)
    client.access_token = "token"
    client.phone_number_id = "phone-id"

    typing_calls: list[str] = []

    async def _ensure_message_id(to: str, message_id: str | None) -> str | None:
        del to
        return message_id

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        return {"messages": [{"id": "wa-msg-2"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        typing_calls.append(message_id)
        return {}

    monkeypatch.setattr(client, "_ensure_message_id", _ensure_message_id)
    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr(settings, "whatsapp_typing_indicator_delay_ms", 0)

    result = await client.send_text(
        to="2348000000000",
        text="Hello",
        message_id="wamid.124",
    )

    assert result["messages"][0]["id"] == "wa-msg-2"
    assert typing_calls == ["wamid.124"]


@pytest.mark.asyncio
async def test_whatsapp_client_send_text_waits_briefly_after_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WhatsAppClient.__new__(WhatsAppClient)
    client.access_token = "token"
    client.phone_number_id = "phone-id"

    events: list[str] = []

    async def _ensure_message_id(to: str, message_id: str | None) -> str | None:
        del to
        return message_id

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        events.append(f"send:{payload['text']['body']}")
        return {"messages": [{"id": "wa-msg-3"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        events.append(f"typing:{message_id}")
        return {}

    async def _sleep(delay_seconds: float) -> None:
        events.append(f"sleep:{delay_seconds}")

    monkeypatch.setattr(client, "_ensure_message_id", _ensure_message_id)
    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr("shared.clients.whatsapp.client.asyncio.sleep", _sleep)
    monkeypatch.setattr(settings, "whatsapp_typing_indicator_delay_ms", 650)

    result = await client.send_text(
        to="2348000000000",
        text="Hello",
        message_id="wamid.125",
    )

    assert result["messages"][0]["id"] == "wa-msg-3"
    assert events == ["typing:wamid.125", "sleep:0.65", "send:Hello"]
