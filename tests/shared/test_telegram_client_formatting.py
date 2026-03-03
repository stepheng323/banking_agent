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


@pytest.mark.asyncio
async def test_send_message_draft_calls_telegram_draft_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
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
