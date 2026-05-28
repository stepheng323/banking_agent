"""Trusted LLM modifier evidence helpers for context-frame replay."""

import re

from shared.types.planner import ContextFrameReplayModifier

CONTEXT_FRAME_REPLAY_MODIFIER_MIN_CONFIDENCE = 0.72


def _contains_replay_modifier_evidence(text: str | None, evidence: str | None) -> bool:
    if not text or not evidence:
        return False

    normalized_text = re.sub(r"\s+", " ", text).strip().casefold()
    normalized_evidence = re.sub(r"\s+", " ", evidence).strip().casefold()
    if not normalized_evidence:
        return False
    return normalized_evidence in normalized_text


def _trusted_replay_modifier(modifier: ContextFrameReplayModifier | None) -> ContextFrameReplayModifier | None:
    if modifier is None:
        return None
    if modifier.confidence < CONTEXT_FRAME_REPLAY_MODIFIER_MIN_CONFIDENCE:
        return None
    return modifier


def _modifier_amount_override(text: str | None, modifier: ContextFrameReplayModifier | None) -> float | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or trusted.amount is None:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.amount_evidence):
        return None
    return trusted.amount if trusted.amount > 0 else None


def _modifier_source_account_candidate(
    text: str | None,
    modifier: ContextFrameReplayModifier | None,
) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.source_account_reference:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.source_account_evidence):
        return None
    return trusted.source_account_reference.strip() or None


def _modifier_narration_candidate(
    text: str | None,
    modifier: ContextFrameReplayModifier | None,
) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.narration:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.narration_evidence):
        return None
    return trusted.narration.strip() or None


__all__ = [
    "CONTEXT_FRAME_REPLAY_MODIFIER_MIN_CONFIDENCE",
    "_contains_replay_modifier_evidence",
    "_modifier_amount_override",
    "_modifier_narration_candidate",
    "_modifier_source_account_candidate",
    "_trusted_replay_modifier",
]
