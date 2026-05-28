"""Telegram draft-message delivery helpers."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from shared.clients.abstractions.messaging import MessageResult
from shared.clients.telegram.payloads import build_message_draft_payload
from shared.utils.logging import get_logger

_DRAFT_UNSUPPORTED_STATUS_CODES = {400, 404, 405, 501}

ApiCall = Callable[..., Awaitable[dict[str, Any]]]
SendText = Callable[..., Awaitable[MessageResult]]
SendDraft = Callable[..., Awaitable[bool]]
DraftSupportedState = Callable[[], bool]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DraftSendResult:
    sent: bool
    draft_supported: bool


async def send_message_draft(
    *,
    api_call: ApiCall,
    to: str,
    text: str,
    draft_supported: bool,
) -> DraftSendResult:
    """Set a draft message in chat using Telegram Bot API sendMessageDraft."""
    if not draft_supported:
        return DraftSendResult(sent=False, draft_supported=False)

    draft_text = (text or "").strip()
    if not draft_text:
        return DraftSendResult(sent=False, draft_supported=draft_supported)

    payload = build_message_draft_payload(to=to, text=draft_text)
    logger.info(
        "telegram_draft_attempt_started",
        channel="telegram",
        method="sendMessageDraft",
    )
    try:
        await api_call("sendMessageDraft", payload, max_retries=1)
        logger.info(
            "telegram_draft_attempt_succeeded",
            channel="telegram",
            method="sendMessageDraft",
        )
        return DraftSendResult(sent=True, draft_supported=draft_supported)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in _DRAFT_UNSUPPORTED_STATUS_CODES:
            logger.warning(
                "telegram_draft_endpoint_unsupported_disabled",
                channel="telegram",
                method="sendMessageDraft",
                http_status=status,
                runtime_draft_enabled=False,
            )
            return DraftSendResult(sent=False, draft_supported=False)
        logger.warning(
            "telegram_draft_attempt_failed",
            channel="telegram",
            method="sendMessageDraft",
            http_status=status,
            runtime_draft_enabled=draft_supported,
            fallback_to_final_send=True,
            error=str(e),
        )
        return DraftSendResult(sent=False, draft_supported=draft_supported)
    except Exception as e:
        logger.warning(
            "telegram_draft_attempt_failed",
            channel="telegram",
            method="sendMessageDraft",
            runtime_draft_enabled=draft_supported,
            fallback_to_final_send=True,
            error=str(e),
        )
        return DraftSendResult(sent=False, draft_supported=draft_supported)


async def send_text_streamed(
    *,
    to: str,
    text: str,
    message_id: str | None,
    draft_supported: bool,
    is_draft_supported: DraftSupportedState,
    send_text: SendText,
    send_message_draft: SendDraft,
    draft_step_chars: int = 120,
    max_draft_updates: int = 12,
    draft_delay_seconds: float = 0.2,
) -> MessageResult:
    """Stream a response as Telegram drafts, then publish the final message."""
    clean_text = (text or "").strip()
    draft_attempted = False
    draft_failed = False
    if clean_text:
        clipped = clean_text[:4096]
        sent = 0
        cursor = min(len(clipped), max(1, draft_step_chars))
        draft_enabled = draft_supported
        while cursor < len(clipped) and sent < max_draft_updates and draft_enabled:
            draft_attempted = True
            draft_enabled = await send_message_draft(to=to, text=clipped[:cursor])
            if not draft_enabled:
                draft_failed = True
            sent += 1
            if draft_delay_seconds > 0:
                await asyncio.sleep(draft_delay_seconds)
            cursor = min(len(clipped), cursor + max(1, draft_step_chars))
        if draft_enabled:
            draft_attempted = True
            draft_enabled = await send_message_draft(to=to, text=clipped)
            if not draft_enabled:
                draft_failed = True
        if draft_attempted and draft_failed:
            logger.info(
                "telegram_draft_fallback_to_final_send",
                channel="telegram",
                method="sendMessageDraft",
                runtime_draft_enabled=is_draft_supported(),
            )

    return await send_text(to=to, text=text, message_id=message_id)
