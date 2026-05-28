from typing import Any

import httpx
import pytest

import shared.clients.whatsapp.client as whatsapp_client_module
import shared.clients.whatsapp.typing as whatsapp_typing
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings


@pytest.mark.asyncio
async def test_whatsapp_client_send_text_suppresses_typing_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WhatsAppClient.__new__(WhatsAppClient)
    client.access_token = "token"
    client.phone_number_id = "phone-id"

    typing_calls: list[str] = []
    sleep_calls: list[float] = []

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        return {"messages": [{"id": "wa-msg-1"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        typing_calls.append(message_id)
        return {}

    async def _sleep(delay_seconds: float) -> None:
        sleep_calls.append(delay_seconds)

    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr(whatsapp_typing.asyncio, "sleep", _sleep)
    monkeypatch.setattr(settings.whatsapp, "typing_indicator_delay_ms", 650)

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

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        return {"messages": [{"id": "wa-msg-2"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        typing_calls.append(message_id)
        return {}

    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr(settings.whatsapp, "typing_indicator_delay_ms", 0)

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

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        events.append(f"send:{payload['text']['body']}")
        return {"messages": [{"id": "wa-msg-3"}], "payload": payload}

    async def _send_typing_indicator(message_id: str) -> dict[str, Any]:
        events.append(f"typing:{message_id}")
        return {}

    async def _sleep(delay_seconds: float) -> None:
        events.append(f"sleep:{delay_seconds}")

    monkeypatch.setattr(client, "_send", _send)
    monkeypatch.setattr(client, "send_typing_indicator", _send_typing_indicator)
    monkeypatch.setattr(whatsapp_typing.asyncio, "sleep", _sleep)
    monkeypatch.setattr(settings.whatsapp, "typing_indicator_delay_ms", 650)

    result = await client.send_text(
        to="2348000000000",
        text="Hello",
        message_id="wamid.125",
    )

    assert result["messages"][0]["id"] == "wa-msg-3"
    assert events == ["typing:wamid.125", "sleep:0.65", "send:Hello"]


@pytest.mark.asyncio
async def test_whatsapp_client_send_flow_data_exchange_omits_action_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WhatsAppClient.__new__(WhatsAppClient)
    client.access_token = "token"
    client.phone_number_id = "phone-id"
    sent_payloads: list[dict[str, Any]] = []

    async def _send(url: str, payload: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        del url, max_retries
        sent_payloads.append(payload)
        return {"messages": [{"id": "wa-flow-1"}]}

    monkeypatch.setattr(client, "_send", _send)

    result = await client.send_flow(
        to="2348000000000",
        flow_id="flow-id",
        flow_config={
            "header": "Authorize",
            "text_body": "Enter PIN",
            "flow_cta": "Authorize",
            "screen_name": "Pin",
            "flow_token": "transfer-pin-idem-1-2348000000000",
            "flow_action": "data_exchange",
        },
        suppress_typing_indicator=True,
    )

    assert result.message_id == "wa-flow-1"
    params = sent_payloads[0]["interactive"]["action"]["parameters"]
    assert params["flow_action"] == "data_exchange"
    assert params["flow_token"] == "transfer-pin-idem-1-2348000000000"
    assert "flow_action_payload" not in params


@pytest.mark.asyncio
async def test_whatsapp_typing_resolves_current_message_id_from_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Redis:
        async def get(self, key: str) -> str:
            assert key == "user:2348000000000:current_message_id"
            return "wamid.redis"

    monkeypatch.setattr(whatsapp_typing.RedisClient, "get_client", staticmethod(lambda: _Redis()))

    assert (
        await whatsapp_typing.resolve_current_message_id(to="2348000000000", message_id=None)
        == "wamid.redis"
    )


@pytest.mark.asyncio
async def test_whatsapp_client_reuses_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.whatsapp, "access_token", "token")
    monkeypatch.setattr(settings.whatsapp, "phone_number_id", "phone-id")
    created_clients: list[object] = []

    class _FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            self.is_closed = False
            self.posts: list[str] = []
            self.gets: list[str] = []
            created_clients.append(self)

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            del kwargs
            self.posts.append(url)
            body: dict[str, object]
            if url.endswith("/media"):
                body = {"id": "media-id"}
            else:
                body = {"messages": [{"id": "wa-msg"}]}
            return httpx.Response(200, request=httpx.Request("POST", url), json=body)

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            del kwargs
            self.gets.append(url)
            if url.endswith("/media-id"):
                return httpx.Response(200, request=httpx.Request("GET", url), json={"url": "https://media.example/file"})
            return httpx.Response(200, request=httpx.Request("GET", url), content=b"media")

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(whatsapp_client_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = WhatsAppClient()

    text_result = await client.send_text("2348000000000", "Hello", suppress_typing_indicator=True)
    image_result = await client.send_image_data("2348000000000", b"image", suppress_typing_indicator=True)
    assert await client.get_media_url("media-id") == "https://media.example/file"
    assert await client.download_media("https://media.example/file") == b"media"
    assert text_result["messages"][0]["id"] == "wa-msg"
    assert image_result["messages"][0]["id"] == "wa-msg"

    assert len(created_clients) == 1
    await client.aclose()
    await client.send_text("2348000000000", "Hello again", suppress_typing_indicator=True)
    assert len(created_clients) == 2
