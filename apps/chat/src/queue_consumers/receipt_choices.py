"""Receipt image choice handling for inbound channel messages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.receipts.choice import (
    extract_receipt_choice_job,
    parse_receipt_choice_action,
    receipt_choice_claim_key,
)
from shared.clients.telegram.client import TelegramClient
from shared.messaging.intents import Say
from shared.messaging.outbox import enqueue_outbox_intents
from shared.models.messages import ChannelMessage
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)
_TELEGRAM_CHANNEL = "telegram"


def resolve_actionable_message_repo(orchestrator: OrchestratorAgent | None) -> Any | None:
    if orchestrator is None:
        return None

    deps = getattr(orchestrator, "deps", None)
    repo = getattr(deps, "actionable_message_repo", None)
    if repo is not None:
        return repo

    handler = getattr(orchestrator, "orchestrator_handler", None)
    return getattr(handler, "actionable_message_repo", None)


async def remove_telegram_inline_keyboard(
    *,
    message: ChannelMessage,
    channel_user_id: str,
    clicked_message_id: str,
    telegram_client_factory: Callable[[], TelegramClient],
) -> None:
    if message.channel != _TELEGRAM_CHANNEL or not clicked_message_id:
        return

    try:
        await telegram_client_factory().remove_inline_keyboard(channel_user_id, clicked_message_id)
    except Exception as exc:
        logger.warning(
            "receipt_choice_telegram_keyboard_remove_failed",
            channel=message.channel,
            channel_user_id_hash=log_fingerprint(channel_user_id),
            clicked_message_id_hash=log_fingerprint(clicked_message_id),
            error_type=type(exc).__name__,
        )


async def release_receipt_choice_claim(
    *,
    redis_client: Any | None,
    channel: str,
    clicked_message_id: str,
) -> None:
    if redis_client is None:
        return
    try:
        delete = getattr(redis_client, "delete", None)
        if delete is not None:
            await delete(receipt_choice_claim_key(channel, clicked_message_id))
    except Exception as exc:
        logger.warning(
            "receipt_choice_claim_release_failed",
            channel=channel,
            clicked_message_id_hash=log_fingerprint(clicked_message_id),
            error=str(exc),
        )


async def handle_receipt_image_choice(
    *,
    message: ChannelMessage,
    orchestrator: OrchestratorAgent | None,
    user: Any,
    phone_number: str,
    channel_user_id: str,
    text: str,
    publisher: QueuePublisher,
    redis_client: Any | None,
    telegram_client_factory: Callable[[], TelegramClient],
) -> dict[str, Any] | None:
    if not parse_receipt_choice_action(text):
        return None

    locale = (await LocaleManager.get_effective_locale(phone_number)).value
    clicked_message_id = str(message.quoted_message_id or message.message_id or "").strip()
    actionable_repo = resolve_actionable_message_repo(orchestrator)

    if not clicked_message_id or actionable_repo is None:
        await enqueue_outbox_intents(
            publisher,
            channel_user_id,
            message.channel,
            [Say(text=render_message("query.receipt.failed", locale))],
            metadata={"source": "receipt_choice", "message_id": message.message_id},
        )
        return {"status": "receipt_image_failed", "reason": "actionable_unavailable"}

    actionable = await actionable_repo.get_by_channel_message_id_for_user(
        clicked_message_id,
        str(getattr(user, "id", "")),
    )
    receipt_job = extract_receipt_choice_job(getattr(actionable, "message_data", None))
    if receipt_job is None:
        await remove_telegram_inline_keyboard(
            message=message,
            channel_user_id=channel_user_id,
            clicked_message_id=clicked_message_id,
            telegram_client_factory=telegram_client_factory,
        )
        await enqueue_outbox_intents(
            publisher,
            channel_user_id,
            message.channel,
            [Say(text=render_message("query.receipt.expired", locale))],
            metadata={"source": "receipt_choice", "message_id": message.message_id},
        )
        return {"status": "receipt_image_expired"}

    if redis_client is not None:
        try:
            claimed = await redis_client.set(
                receipt_choice_claim_key(message.channel, clicked_message_id),
                "1",
                nx=True,
            )
        except Exception as exc:
            logger.warning(
                "receipt_choice_claim_failed",
                channel=message.channel,
                clicked_message_id_hash=log_fingerprint(clicked_message_id),
                error=str(exc),
            )
        else:
            if not claimed:
                await remove_telegram_inline_keyboard(
                    message=message,
                    channel_user_id=channel_user_id,
                    clicked_message_id=clicked_message_id,
                    telegram_client_factory=telegram_client_factory,
                )
                await enqueue_outbox_intents(
                    publisher,
                    channel_user_id,
                    message.channel,
                    [Say(text=render_message("query.receipt.already_generating", locale))],
                    metadata={"source": "receipt_choice", "message_id": message.message_id},
                )
                return {"status": "receipt_image_already_generating"}

    receipt_job = dict(receipt_job)
    receipt_job["language"] = locale
    receipt_job["send_generation_notice"] = True

    try:
        await publisher.publish("receipt.process", receipt_job)
    except Exception:
        logger.error(
            "receipt_choice_publish_failed",
            channel=message.channel,
            channel_user_id=channel_user_id,
            message_id=message.message_id,
            exc_info=True,
        )
        await release_receipt_choice_claim(
            redis_client=redis_client,
            channel=message.channel,
            clicked_message_id=clicked_message_id,
        )
        await enqueue_outbox_intents(
            publisher,
            channel_user_id,
            message.channel,
            [Say(text=render_message("query.receipt.failed", locale))],
            metadata={"source": "receipt_choice", "message_id": message.message_id},
        )
        return {"status": "receipt_image_failed", "reason": "publish_failed"}

    await remove_telegram_inline_keyboard(
        message=message,
        channel_user_id=channel_user_id,
        clicked_message_id=clicked_message_id,
        telegram_client_factory=telegram_client_factory,
    )
    return {"status": "receipt_image_accepted"}
