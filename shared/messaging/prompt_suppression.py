"""Shared metadata for suppressing stale pending-input prompts."""

from __future__ import annotations

from typing import Any

PENDING_INPUT_PROMPT_KIND = "pending_input"
PENDING_INPUT_PROMPT_METADATA_KEY = "pending_input_prompt"
PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY = "pending_input_prompt_origin_message_id"
PENDING_INPUT_PROMPT_THREAD_KEY = "pending_input_prompt_thread_key"


def latest_inbound_delivery_target_key(channel: str, delivery_target: str) -> str:
    """Redis key storing the newest accepted inbound message for a delivery target."""
    normalized_channel = (channel or "unknown").strip() or "unknown"
    normalized_target = (delivery_target or "unknown").strip() or "unknown"
    return f"chat:latest-inbound-target:{normalized_channel}:{normalized_target}"


def pending_input_prompt_metadata(
    *,
    channel: str,
    delivery_target: str,
    origin_message_id: str,
) -> dict[str, Any]:
    """Build delivery metadata used by the receipt worker stale-prompt guard."""
    return {
        PENDING_INPUT_PROMPT_METADATA_KEY: True,
        PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY: str(origin_message_id),
        PENDING_INPUT_PROMPT_THREAD_KEY: latest_inbound_delivery_target_key(channel, delivery_target),
    }


def is_pending_input_prompt_metadata(metadata: dict[str, Any]) -> bool:
    """Return whether queued delivery metadata describes a pending-input prompt."""
    return bool(metadata.get(PENDING_INPUT_PROMPT_METADATA_KEY))
