"""Shared correction-marker detection for conversational turns.

A user can correct themselves with assertive or advisory prefixes. Detecting
those prefixes once avoids every subsystem growing its own slightly different
vocabulary. The marker says "the previous turn should be reinterpreted"; each
consumer still decides what that reinterpretation means in its own domain.
"""

from __future__ import annotations

CORRECTION_PREFIXES: tuple[str, ...] = (
    "i said ",
    "i mean ",
    "i meant ",
    "i asked for ",
    "no, ",
    "no ",
    "not ",
    "instead ",
    "rather ",
    "make it ",
    "change it to ",
    "use ",
    "actually ",
    "wait ",
    "wait, ",
)

ASSERTIVE_CORRECTION_PREFIXES: tuple[str, ...] = (
    "i said ",
    "i mean ",
    "i meant ",
    "i asked for ",
)


def _normalize(message: str) -> str:
    return " ".join(message.strip().casefold().split())


def has_correction_prefix(
    message: str,
    *,
    prefixes: tuple[str, ...] = CORRECTION_PREFIXES,
) -> bool:
    """Return True when the message starts with a known correction marker."""
    normalized = _normalize(message)
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in prefixes)


def strip_correction_prefix(
    message: str,
    *,
    prefixes: tuple[str, ...] = CORRECTION_PREFIXES,
) -> str | None:
    """Return the message residue after a correction prefix, or None if none."""
    normalized = _normalize(message)
    if not normalized:
        return None
    for prefix in prefixes:
        if normalized.startswith(prefix):
            residue = normalized[len(prefix) :].strip("?.!, ")
            return residue if residue else None
    return None


def has_assertive_correction_prefix(message: str) -> bool:
    """Return True for explicit self-corrections like "I meant ..."."""
    return has_correction_prefix(message, prefixes=ASSERTIVE_CORRECTION_PREFIXES)


SCOPE_BROADENING_TOKENS: tuple[str, ...] = (
    "whole",
    "all",
    "everything",
    "every",
    "full",
    "total",
    "entire",
)


def is_scope_broadening_correction(message: str) -> bool:
    """Detect corrections like "I mean my whole spending" that widen the active scope."""
    if not has_assertive_correction_prefix(message):
        return False
    residue = strip_correction_prefix(message, prefixes=ASSERTIVE_CORRECTION_PREFIXES)
    if not residue:
        return False
    return any(token in residue for token in SCOPE_BROADENING_TOKENS)


__all__ = [
    "ASSERTIVE_CORRECTION_PREFIXES",
    "CORRECTION_PREFIXES",
    "SCOPE_BROADENING_TOKENS",
    "has_assertive_correction_prefix",
    "has_correction_prefix",
    "is_scope_broadening_correction",
    "strip_correction_prefix",
]
