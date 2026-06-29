import httpx
import pytest

import shared.clients.telegram.client as telegram_client_module
import shared.clients.telegram.mini_app as telegram_mini_app
from shared.clients.telegram.client import TelegramClient
from shared.clients.telegram.formatting import format_telegram_html, telegram_html_to_plain_text
from shared.config.settings import settings


def test_telegram_html_formatter_escapes_html_and_formats_markdown() -> None:
    rendered = format_telegram_html("*Bold* _italics_ `code` <tag>")

    assert "<b>Bold</b>" in rendered
    assert "<i>italics</i>" in rendered
    assert "<code>code</code>" in rendered
    assert "&lt;tag&gt;" in rendered


def test_telegram_html_formatter_does_not_break_plain_text() -> None:
    rendered = format_telegram_html("Which account would you like to use?")
    assert rendered == "Which account would you like to use?"


def test_telegram_html_formatter_handles_double_asterisk_bold() -> None:
    rendered = format_telegram_html("**Ticket:** 123\n*Total:* **₦30,000**")
    assert rendered == "<b>Ticket:</b> 123\n<b>Total:</b> <b>₦30,000</b>"


def test_telegram_html_to_plain_text_strips_markup_for_mini_app_copy() -> None:
    rendered = telegram_html_to_plain_text("<b>₦3,000 -&gt; Ada</b>\n<code>GTBank</code>")

    assert rendered == "₦3,000 -> Ada\nGTBank"


@pytest.mark.asyncio
async def test_telegram_client_reuses_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
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
            return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True, "result": True})

        async def get(self, url: str) -> httpx.Response:
            self.gets.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), content=b"media")

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr(telegram_client_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = TelegramClient()

    await client._call("sendMessage", {"chat_id": "12345", "text": "Hi"})
    await client._call("sendChatAction", {"chat_id": "12345", "action": "typing"})
    content = await client.download_media("https://api.telegram.org/file/bottest/media.jpg")

    assert content == b"media"
    assert len(created_clients) == 1
    await client.aclose()
    await client._call("sendMessage", {"chat_id": "12345", "text": "Again"})
    assert len(created_clients) == 2


@pytest.mark.asyncio
async def test_send_mini_app_routes_tokens_to_expected_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_mini_app_base_url", "https://mini.example.test")
    client = TelegramClient()

    calls: list[dict[str, object]] = []
    bootstraps: list[dict[str, object]] = []

    async def _fake_bootstrap(**kwargs: object) -> str:
        bootstraps.append(dict(kwargs))
        return f"boot-{kwargs['endpoint']}"

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files, max_retries
        assert method == "sendMessage"
        calls.append(payload or {})
        return {"ok": True, "result": {"message_id": 55}}

    monkeypatch.setattr(client, "_call", _fake_call)
    monkeypatch.setattr(telegram_mini_app, "create_telegram_miniapp_bootstrap", _fake_bootstrap)

    await client.send_mini_app(to="12345", flow_token="link-opaque-token")
    await client.send_mini_app(to="12345", flow_token="onboarding-opaque-token")
    await client.send_mini_app(to="12345", flow_token="transfer-pin-idem-12345")

    assert len(calls) == 3

    first_markup = calls[0]["reply_markup"]
    first_url = first_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/linking.html" in first_url
    assert "boot=boot-linking" in first_url
    assert "flow_token=" not in first_url
    assert "chat_id=" not in first_url

    second_markup = calls[1]["reply_markup"]
    second_url = second_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/onboarding.html" in second_url
    assert "boot=boot-onboarding" in second_url
    assert "flow_token=onboarding-opaque-token" not in second_url
    assert "chat_id=" not in second_url

    third_markup = calls[2]["reply_markup"]
    third_url = third_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/pin_entry.html" in third_url
    assert "boot=boot-pin" in third_url
    assert "flow_token=" not in third_url
    assert "chat_id=12345" not in third_url
    assert bootstraps == [
        {"chat_id": "12345", "flow_token": "link-opaque-token", "endpoint": "linking", "extra": {}},
        {"chat_id": "12345", "flow_token": "onboarding-opaque-token", "endpoint": "onboarding", "extra": {}},
        {"chat_id": "12345", "flow_token": "transfer-pin-idem-12345", "endpoint": "pin", "extra": {}},
    ]


@pytest.mark.asyncio
async def test_send_mini_app_keeps_pin_details_in_chat_not_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_mini_app_base_url", "https://mini.example.test")
    client = TelegramClient()

    bootstraps: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    async def _fake_bootstrap(**kwargs: object) -> str:
        bootstraps.append(dict(kwargs))
        return "boot-pin"

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files, max_retries
        assert method == "sendMessage"
        captured.update(payload or {})
        return {"ok": True, "result": {"message_id": 56}}

    monkeypatch.setattr(client, "_call", _fake_call)
    monkeypatch.setattr(telegram_mini_app, "create_telegram_miniapp_bootstrap", _fake_bootstrap)

    await client.send_mini_app(
        to="12345",
        flow_token="transfer-pin-idem-12345",
        header="Authorize Transfer",
        body_text="<b>₦3,000 -&gt; Ada</b>\n<code>GTBank</code>",
        cta_text="Authorize",
    )

    assert captured["text"] == "<b>Authorize Transfer</b>\n\n<b>₦3,000 -&gt; Ada</b>\n<code>GTBank</code>"
    assert bootstraps == [
        {
            "chat_id": "12345",
            "flow_token": "transfer-pin-idem-12345",
            "endpoint": "pin",
            "extra": {"submit_label": "Authorize"},
        }
    ]


@pytest.mark.asyncio
async def test_send_interactive_uses_object_reply_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    client = TelegramClient()

    captured: dict[str, object] = {}

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files, max_retries
        assert method == "sendMessage"
        captured.update(payload or {})
        return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_interactive(
        to="12345",
        body_text="Choose one",
        options=[{"id": "1", "title": "First"}],
    )

    assert result.success is True
    assert captured["reply_markup"] == {"inline_keyboard": [[{"text": "First", "callback_data": "1"}]]}


@pytest.mark.asyncio
async def test_send_interactive_groups_compact_option_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    client = TelegramClient()

    captured: dict[str, object] = {}

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files, max_retries
        assert method == "sendMessage"
        captured.update(payload or {})
        return {"ok": True, "result": {"message_id": 78}}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_interactive(
        to="12345",
        body_text="Choose one",
        options=[
            {"id": "1", "title": "1"},
            {"id": "2", "title": "2"},
            {"id": "3", "title": "3"},
            {"id": "4", "title": "4"},
            {"id": "5", "title": "5"},
        ],
    )

    assert result.success is True
    assert captured["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "1", "callback_data": "1"}],
            [{"text": "2", "callback_data": "2"}],
            [{"text": "3", "callback_data": "3"}],
            [{"text": "4", "callback_data": "4"}],
            [{"text": "5", "callback_data": "5"}],
        ]
    }


@pytest.mark.asyncio
async def test_remove_inline_keyboard_edits_reply_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    client = TelegramClient()

    captured: dict[str, object] = {}

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files
        assert method == "editMessageReplyMarkup"
        assert max_retries == 1
        captured.update(payload or {})
        return {"ok": True, "result": True}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.remove_inline_keyboard("12345", "99")

    assert result is True
    assert captured == {"chat_id": "12345", "message_id": "99"}
