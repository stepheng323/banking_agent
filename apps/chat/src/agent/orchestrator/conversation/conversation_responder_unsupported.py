"""Unsupported-capability fallback handling for conversational replies."""

from __future__ import annotations

import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    localized_supported_alternatives,
)
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import message_key_exists, render_message

UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT = "unsupported_capability_followup"
UNSUPPORTED_CAPABILITY_PROMISE_RE = re.compile(
    r"\b(?:"
    r"(?:i(?:'ll| will)|let me|i can|we can)\s+"
    r"(?:lend|loan|borrow|arrange|approve|process|give|sort|buy|sell|trade|invest|export|download|send)|"
    r"(?:loan|lend|borrow|credit|investment|crypto|bitcoin|stock|pdf|csv)\s+"
    r"(?:approved|available|processing|coming|ready|sent)|"
    r"(?:buy|sell|trade|recommend)\s+(?:bitcoin|crypto|stocks?|shares?|investments?)|"
    r"(?:export|download|generate)\s+(?:a\s+)?(?:pdf|csv|statement|spreadsheet)|"
    r"(?:send|transfer)\s+(?:money\s+)?(?:abroad|internationally)|"
    r"i\s+can\s+do\s+that"
    r")\b",
    re.IGNORECASE,
)


def unsupported_capability_fallback_reply(user_ctx: dict[str, Any] | None, locale: str) -> str:
    unsupported = user_ctx.get("unsupported_capability") if isinstance(user_ctx, dict) else None
    if not isinstance(unsupported, dict):
        unsupported = {
            "capability": "that capability",
            "supported": localized_supported_alternatives(locale),
        }
    cap_key = ""
    if isinstance(unsupported, dict):
        cap_key = str(unsupported.get("key") or unsupported.get("capability_key") or "")

    base_key = "capability.unsupported_unavailable_followup"
    message_key = f"{base_key}_{cap_key}" if cap_key else base_key
    if not message_key_exists(message_key, locale):
        message_key = base_key

    return render_message(
        cast(MessageKey, message_key),
        locale,
        unsupported_capability_params(unsupported, locale=locale),
    )


__all__ = [
    "UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT",
    "UNSUPPORTED_CAPABILITY_PROMISE_RE",
    "unsupported_capability_fallback_reply",
]
