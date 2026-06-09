"""Messaging channel identifiers."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias


class MessagingChannel(StrEnum):
    """Supported inbound/outbound messaging channels."""

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


ChannelInput: TypeAlias = MessagingChannel | str

DEFAULT_MESSAGING_CHANNEL = MessagingChannel.WHATSAPP


def normalize_messaging_channel(
    channel: ChannelInput | None,
    *,
    default: MessagingChannel = DEFAULT_MESSAGING_CHANNEL,
) -> MessagingChannel:
    """Normalize a raw channel value at an ingress boundary."""
    if isinstance(channel, MessagingChannel):
        return channel
    raw = str(channel or default.value).strip().lower()
    if not raw:
        return default
    return MessagingChannel(raw)


__all__ = [
    "ChannelInput",
    "DEFAULT_MESSAGING_CHANNEL",
    "MessagingChannel",
    "normalize_messaging_channel",
]
