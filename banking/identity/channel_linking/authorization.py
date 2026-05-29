"""Pure contracts and token helpers for channel-link PIN authorization."""

from dataclasses import dataclass
from typing import Any, Literal

CHANNEL_LINK_PIN_PREFIX = "channel-link-pin-"
CHANNEL_LINK_TRANSACTION_TYPE = "channel_link"
CHANNEL_LINK_SESSION_PURPOSE = "channel_identity_link"

ChannelLinkStatus = Literal[
    "success",
    "expired",
    "invalid_token",
    "invalid_session",
    "wrong_authorizer",
    "invalid_pin",
    "identity_claimed",
    "failed",
]


@dataclass(slots=True)
class ChannelLinkPinResult:
    success: bool
    status: ChannelLinkStatus
    error: str = ""
    attempts_remaining: int = 3
    locked: bool = False
    requested_channel: str = ""
    requested_channel_user_id: str = ""
    user: Any | None = None


def build_channel_link_pin_token(channel_link_session_token: str) -> str:
    return f"{CHANNEL_LINK_PIN_PREFIX}{channel_link_session_token}"


def parse_channel_link_pin_token(flow_token: str | None) -> str | None:
    token = str(flow_token or "")
    if not token.startswith(CHANNEL_LINK_PIN_PREFIX):
        return None
    session_token = token.removeprefix(CHANNEL_LINK_PIN_PREFIX)
    return session_token or None


def is_channel_link_pin_token(flow_token: str | None) -> bool:
    return parse_channel_link_pin_token(flow_token) is not None
