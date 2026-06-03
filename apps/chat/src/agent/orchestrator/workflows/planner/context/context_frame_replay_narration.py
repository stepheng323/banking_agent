"""Narration overrides for context-frame replay modifiers."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_amounts import (
    _text_is_replay_amount_token,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_modifier_core import (
    _modifier_narration_candidate,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_modifier_text import (
    _clean_replay_modifier_text,
)
from banking.transactions.shared.account_selection.reference import match_source_account_reference
from shared.types.planner import ContextFrameReplayModifier

_REPLAY_NARRATION_RE = re.compile(
    r"\b(?:with\s+)?(?:narration|memo|note|description|reason|purpose|"
    r"akosile|bayani|bayanin|nkowa)\b\s*"
    r"(?:as|to|is|:)?\s*(?P<explicit>[^.?!;\n]{1,120})|"
    r"\b(?:for|fun|domin|saboda|maka)\s+(?P<for_note>[^.?!;\n]{1,120})",
    re.IGNORECASE,
)


def _replay_narration_override(
    text: str | None,
    *,
    accounts: list[dict[str, Any]] | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> str | None:
    if not text:
        return None

    for match in _REPLAY_NARRATION_RE.finditer(text):
        is_bare_for = match.group("for_note") is not None
        candidate = _clean_replay_modifier_text(match.group("explicit") or match.group("for_note"))
        if not candidate:
            continue
        if is_bare_for:
            if _text_is_replay_amount_token(candidate):
                continue
            if accounts and match_source_account_reference(candidate, accounts):
                continue
        return candidate
    return _modifier_narration_candidate(text, replay_modifier)


__all__ = [
    "_replay_narration_override",
]
