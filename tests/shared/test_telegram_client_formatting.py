import httpx
import pytest

from shared.clients.abstractions.messaging import MessageResult
from shared.clients.telegram.client import TelegramClient, _format_telegram_html
from shared.config.settings import settings


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
    monkeypatch.setattr(settings, "telegram_mini_app_base_url", "https://mini.fusepay.dev")
    client = TelegramClient()

    calls: list[dict[str, object]] = []

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

    await client.send_mini_app(to="12345", flow_token="link-12345-1700000000")
    await client.send_mini_app(to="12345", flow_token="onboarding-12345")
    await client.send_mini_app(to="12345", flow_token="transfer-pin-idem-12345")

    assert len(calls) == 3

    first_markup = calls[0]["reply_markup"]
    first_url = first_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/linking.html" in first_url

    second_markup = calls[1]["reply_markup"]
    second_url = second_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/onboarding.html" in second_url

    third_markup = calls[2]["reply_markup"]
    third_url = third_markup["inline_keyboard"][0][0]["web_app"]["url"]
    assert "/static/telegram/pin_entry.html" in third_url


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
    assert captured["reply_markup"] == {
        "inline_keyboard": [[{"text": "First", "callback_data": "1"}]]
    }
