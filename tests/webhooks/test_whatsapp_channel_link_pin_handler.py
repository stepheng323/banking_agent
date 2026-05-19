import json
from typing import Any

import pytest

from apps.gateway.api.webhooks.whatsapp.flows.handlers import channel_link_pin_handler as handler_module
from apps.gateway.api.webhooks.whatsapp.flows.handlers.channel_link_pin_handler import handle_channel_link_pin
from shared.services.channel_linking import ChannelLinkPinResult


class _TelegramClientStub:
    def __init__(self) -> None:
        self.text_calls: list[dict[str, Any]] = []

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"ok": True}


@pytest.mark.asyncio
async def test_whatsapp_channel_link_pin_success_notifies_requested_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _complete_channel_link_with_pin(**kwargs: Any) -> ChannelLinkPinResult:
        assert kwargs["flow_token"] == "channel-link-pin-channel-link-token"
        assert kwargs["pin"] == "1234"
        assert kwargs["authorizing_channel"] == "whatsapp"
        assert kwargs["authorizing_channel_user_id"] == "2348162511023"
        return ChannelLinkPinResult(
            success=True,
            status="success",
            requested_channel="telegram",
            requested_channel_user_id="12345",
        )

    telegram_client = _TelegramClientStub()
    monkeypatch.setattr(handler_module, "complete_channel_link_with_pin", _complete_channel_link_with_pin)
    monkeypatch.setattr(handler_module, "TelegramClient", lambda: telegram_client)

    response = await handle_channel_link_pin(
        {"pin": "1234"},
        "channel-link-pin-channel-link-token",
        False,
        b"",
        b"",
        authorizing_channel_user_id="2348162511023",
    )

    body = json.loads(response.body)
    assert body["screen"] == "SUCCESS"
    assert body["data"]["extension_message_response"]["params"]["success"] == "true"
    assert telegram_client.text_calls == [
        {
            "to": "12345",
            "text": "Your Telegram account has been linked. You can now use banking features here.",
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_channel_link_pin_invalid_pin_stays_on_pin_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _complete_channel_link_with_pin(**kwargs: Any) -> ChannelLinkPinResult:
        del kwargs
        return ChannelLinkPinResult(
            success=False,
            status="invalid_pin",
            error="Invalid PIN. 2 attempt(s) remaining.",
            attempts_remaining=2,
        )

    monkeypatch.setattr(handler_module, "complete_channel_link_with_pin", _complete_channel_link_with_pin)

    response = await handle_channel_link_pin(
        {"pin": "0000"},
        "channel-link-pin-channel-link-token",
        False,
        b"",
        b"",
    )

    body = json.loads(response.body)
    assert body == {
        "version": "3.0",
        "screen": "Pin",
        "data": {
            "show_error": True,
            "error_message": "Invalid PIN. 2 attempt(s) remaining.",
            "attempts_remaining": 2,
            "locked": False,
        },
    }
