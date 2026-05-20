import httpx
import pytest

from shared.clients.abstractions.messaging import MessageResult
from shared.clients.telegram import client as telegram_client_module
from shared.clients.telegram.client import TelegramClient, _format_telegram_html, _telegram_html_to_plain_text
from shared.config.settings import Settings, settings


def test_telegram_html_formatter_escapes_html_and_formats_markdown() -> None:
    rendered = _format_telegram_html("*Bold* _italics_ `code` <tag>")

    assert "<b>Bold</b>" in rendered
    assert "<i>italics</i>" in rendered
    assert "<code>code</code>" in rendered
    assert "&lt;tag&gt;" in rendered


def test_telegram_html_formatter_does_not_break_plain_text() -> None:
    rendered = _format_telegram_html("Which account would you like to use?")
    assert rendered == "Which account would you like to use?"


def test_telegram_html_formatter_handles_double_asterisk_bold() -> None:
    rendered = _format_telegram_html("**Ticket:** 123\n*Total:* **₦30,000**")
    assert rendered == "<b>Ticket:</b> 123\n<b>Total:</b> <b>₦30,000</b>"


def test_telegram_html_to_plain_text_strips_markup_for_mini_app_copy() -> None:
    rendered = _telegram_html_to_plain_text("<b>₦3,000 -&gt; Ada</b>\n<code>GTBank</code>")

    assert rendered == "₦3,000 -> Ada\nGTBank"


def test_telegram_message_draft_defaults_enabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ENABLE_MESSAGE_DRAFT", raising=False)
    loaded = Settings()
    assert loaded.telegram_enable_message_draft is True


def test_telegram_message_draft_respects_false_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLE_MESSAGE_DRAFT", "false")
    loaded = Settings()
    assert loaded.telegram_enable_message_draft is False


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
async def test_send_message_draft_calls_telegram_draft_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_enable_message_draft", True)
    client = TelegramClient()
    calls: list[tuple[str, dict[str, object], int]] = []

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del files
        calls.append((method, payload or {}, max_retries))
        return {"ok": True, "result": True}

    monkeypatch.setattr(client, "_call", _fake_call)

    ok = await client.send_message_draft("12345", "Hello from draft")

    assert ok is True
    assert calls == [("sendMessageDraft", {"chat_id": "12345", "text": "Hello from draft"}, 1)]


@pytest.mark.asyncio
async def test_send_text_streamed_sends_drafts_before_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_enable_message_draft", True)
    client = TelegramClient()
    calls: list[str] = []

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del payload, files, max_retries
        calls.append(method)
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 99}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_text_streamed(
        to="12345",
        text="x" * 280,
        draft_step_chars=100,
        max_draft_updates=2,
        draft_delay_seconds=0,
    )

    assert isinstance(result, MessageResult)
    assert result.success is True
    assert result.message_id == "99"
    assert calls == ["sendMessageDraft", "sendMessageDraft", "sendMessageDraft", "sendMessage"]


@pytest.mark.asyncio
async def test_send_text_streamed_disables_draft_when_endpoint_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_enable_message_draft", True)
    client = TelegramClient()
    calls: list[str] = []
    log_events: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs: object) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: object) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(telegram_client_module, "logger", _Logger())

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del payload, files, max_retries
        calls.append(method)
        if method == "sendMessageDraft":
            request = httpx.Request("POST", "https://api.telegram.org/botTEST/sendMessageDraft")
            response = httpx.Response(status_code=400, request=request)
            raise httpx.HTTPStatusError("400 Bad Request", request=request, response=response)
        return {"ok": True, "result": {"message_id": 100}}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_text_streamed(
        to="12345",
        text="x" * 280,
        draft_step_chars=100,
        max_draft_updates=3,
        draft_delay_seconds=0,
    )

    assert result.success is True
    assert result.message_id == "100"
    assert calls == ["sendMessageDraft", "sendMessage"]
    assert (
        "telegram_draft_endpoint_unsupported_disabled",
        {"channel": "telegram", "method": "sendMessageDraft", "http_status": 400, "runtime_draft_enabled": False},
    ) in log_events
    assert (
        "telegram_draft_fallback_to_final_send",
        {"channel": "telegram", "method": "sendMessageDraft", "runtime_draft_enabled": False},
    ) in log_events

    calls.clear()
    second = await client.send_text_streamed(
        to="12345",
        text="another message",
        draft_step_chars=5,
        max_draft_updates=3,
        draft_delay_seconds=0,
    )
    assert second.success is True
    assert calls == ["sendMessage"]


@pytest.mark.asyncio
async def test_send_text_streamed_logs_fallback_after_unexpected_draft_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_enable_message_draft", True)
    client = TelegramClient()
    calls: list[str] = []
    log_events: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs: object) -> None:
            log_events.append((event, kwargs))

        def warning(self, event: str, **kwargs: object) -> None:
            log_events.append((event, kwargs))

    monkeypatch.setattr(telegram_client_module, "logger", _Logger())

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del payload, files, max_retries
        calls.append(method)
        if method == "sendMessageDraft":
            raise RuntimeError("boom")
        return {"ok": True, "result": {"message_id": 202}}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_text_streamed(
        to="12345",
        text="x" * 280,
        draft_step_chars=100,
        max_draft_updates=3,
        draft_delay_seconds=0,
    )

    assert result.success is True
    assert result.message_id == "202"
    assert calls == ["sendMessageDraft", "sendMessage"]
    assert (
        "telegram_draft_fallback_to_final_send",
        {"channel": "telegram", "method": "sendMessageDraft", "runtime_draft_enabled": True},
    ) in log_events
    assert any(event == "telegram_draft_attempt_failed" for event, _ in log_events)


@pytest.mark.asyncio
async def test_send_text_streamed_skips_draft_when_feature_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_enable_message_draft", False)
    client = TelegramClient()
    calls: list[str] = []

    async def _fake_call(
        method: str,
        payload: dict[str, object] | None = None,
        files: dict[str, object] | None = None,
        max_retries: int = 3,
    ) -> dict[str, object]:
        del payload, files, max_retries
        calls.append(method)
        return {"ok": True, "result": {"message_id": 101}}

    monkeypatch.setattr(client, "_call", _fake_call)

    result = await client.send_text_streamed(
        to="12345",
        text="x" * 280,
        draft_step_chars=100,
        max_draft_updates=3,
        draft_delay_seconds=0,
    )

    assert result.success is True
    assert result.message_id == "101"
    assert calls == ["sendMessage"]


@pytest.mark.asyncio
async def test_send_mini_app_routes_tokens_to_expected_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_mini_app_base_url", "https://mini.narya.ai")
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
    monkeypatch.setattr(telegram_client_module, "create_telegram_miniapp_bootstrap", _fake_bootstrap)

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
    monkeypatch.setattr(settings, "telegram_mini_app_base_url", "https://mini.narya.ai")
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
    monkeypatch.setattr(telegram_client_module, "create_telegram_miniapp_bootstrap", _fake_bootstrap)

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
            "extra": {},
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
            [
                {"text": "1", "callback_data": "1"},
                {"text": "2", "callback_data": "2"},
                {"text": "3", "callback_data": "3"},
            ],
            [
                {"text": "4", "callback_data": "4"},
                {"text": "5", "callback_data": "5"},
            ],
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
