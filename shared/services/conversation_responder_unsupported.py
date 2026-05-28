"""Unsupported-capability fallback handling for conversational replies."""

from __future__ import annotations

import re
from typing import Any

from shared.i18n.renderer import render_message
from shared.services.unsupported_capability_presentation import unsupported_capability_params
from shared.services.unsupported_capability_registry import localized_supported_alternatives

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
    return render_message(
        "capability.unsupported_unavailable_followup",
        locale,
        unsupported_capability_params(unsupported, locale=locale),
    )


__all__ = [
    "UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT",
    "UNSUPPORTED_CAPABILITY_PROMISE_RE",
    "unsupported_capability_fallback_reply",
]
