"""Outbound intent shaping helpers for the message consumer."""

from __future__ import annotations

from typing import Any

from shared.i18n.models import LocaleCode
from shared.i18n.renderer import render_message
from shared.messaging.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from shared.messaging.prompt_suppression import PENDING_INPUT_PROMPT_KIND
from shared.models.messages import ChannelMessage
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


def intent_kind(intent: UiIntent | dict[str, Any]) -> str:
    if isinstance(intent, dict):
        return str(intent.get("type") or "unknown")
    if isinstance(intent, Say):
        return "say"
    if isinstance(intent, RequestAuth):
        return "auth_request"
    if isinstance(intent, RequestConfirmation):
        return "request_confirmation"
    if isinstance(intent, ShowReceipt):
        return "show_receipt"
    if isinstance(intent, ShowFlow):
        return "flow"
    if isinstance(intent, ShowOptions):
        return "show_options"
    return type(intent).__name__.lower()


def intent_text(intent: UiIntent | dict[str, Any]) -> str | None:
    if isinstance(intent, Say):
        return intent.text
    if isinstance(intent, dict) and intent.get("type") == "say":
        text = intent.get("text")
        return text if isinstance(text, str) else None
    return None


def is_default_greeting_text(text: str | None) -> bool:
    if not text:
        return False
    normalized = " ".join(text.split())
    for locale in LocaleCode:
        greeting = str(render_message("conversational.greeting", locale.value))
        if normalized == " ".join(greeting.split()):
            return True
    return False


def suppress_spurious_greeting_intents(intents: list[UiIntent | dict[str, Any]]) -> list[UiIntent | dict[str, Any]]:
    """Drop default greeting only when a substantive response is already queued."""
    has_non_greeting_visible_response = any(
        (text is not None and not is_default_greeting_text(text))
        or isinstance(intent, (RequestAuth, RequestConfirmation, ShowReceipt, ShowFlow, ShowOptions))
        for intent in intents
        for text in [intent_text(intent)]
    )
    if not has_non_greeting_visible_response:
        return intents

    filtered = [intent for intent in intents if not is_default_greeting_text(intent_text(intent))]
    if len(filtered) != len(intents):
        logger.warning(
            "message_consumer_spurious_greeting_suppressed",
            original_count=len(intents),
            filtered_count=len(filtered),
        )
    return filtered


def is_pending_input_prompt_outbox(raw_outbox: Any) -> bool:
    if not isinstance(raw_outbox, list) or not raw_outbox:
        return False
    items = [item for item in raw_outbox if isinstance(item, dict)]
    return len(items) == len(raw_outbox) and all(
        item.get("prompt_kind") == PENDING_INPUT_PROMPT_KIND for item in items
    )


def should_suppress_intermediate_input_prompt(
    *,
    message: ChannelMessage,
    raw_outbox: Any,
    metadata_key: str,
) -> bool:
    metadata = message.channel_metadata if isinstance(message.channel_metadata, dict) else {}
    return bool(metadata.get(metadata_key)) and is_pending_input_prompt_outbox(raw_outbox)


def prepare_orchestrator_outbound(
    orchestrator_output: dict[str, Any],
    *,
    message_id: str | None = None,
) -> tuple[
    list[UiIntent | dict[str, Any]],
    Any,
    str | None,
    dict[str, Any],
]:
    """Choose the outbound intents/raw outbox that should be sent for an orchestrator result."""
    intents: list[UiIntent] = orchestrator_output.get("intents", [])
    raw_outbox = orchestrator_output.get("outbox")
    response_text = orchestrator_output.get("text")
    delivery_metadata = (
        orchestrator_output.get("delivery_metadata")
        if isinstance(orchestrator_output.get("delivery_metadata"), dict)
        else {}
    )

    if intents:
        has_primary_interaction = any(
            isinstance(intent, (RequestAuth, RequestConfirmation, ShowReceipt, ShowOptions, ShowFlow))
            for intent in intents
        )
        if (
            response_text
            and not has_primary_interaction
            and not any(isinstance(intent, Say) for intent in intents)
        ):
            intents.append(Say(text=response_text))
        return list(intents), raw_outbox, response_text, delivery_metadata

    fallback_outbox = [item for item in raw_outbox if isinstance(item, dict)] if isinstance(raw_outbox, list) else []
    if fallback_outbox:
        logger.warning(
            "message_consumer_empty_intents_falling_back_to_raw_outbox",
            outbox_count=len(fallback_outbox),
            message_id=message_id,
        )
    return fallback_outbox, raw_outbox, response_text, delivery_metadata


def log_prepared_outbound(
    *,
    message: ChannelMessage,
    phone_number: str,
    response_text: str | None,
    intents: list[UiIntent | dict[str, Any]],
) -> None:
    text_hashes = [log_fingerprint(text) for intent in intents if (text := intent_text(intent))]
    text_lengths = [len(text) for intent in intents if (text := intent_text(intent))]
    logger.info(
        "message_consumer_outbound_prepared",
        message_id=message.message_id,
        channel=message.channel,
        phone_number=phone_number,
        intent_count=len(intents),
        intent_kinds=[intent_kind(intent) for intent in intents],
        text_hashes=text_hashes,
        text_lengths=text_lengths,
        default_greeting_count=sum(1 for intent in intents if is_default_greeting_text(intent_text(intent))),
        response_text_hash=log_fingerprint(response_text),
        response_text_is_default_greeting=is_default_greeting_text(response_text),
    )
