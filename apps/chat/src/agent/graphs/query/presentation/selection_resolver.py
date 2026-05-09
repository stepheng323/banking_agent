"""Selection lookup and payload-to-query resolution."""

from __future__ import annotations

import re

from apps.chat.src.agent.shared.query_contracts import SelectionPayload, SurfaceView


def find_selection_payload(
    surface_view: SurfaceView | None,
    *,
    index: int | None = None,
    label: str | None = None,
) -> SelectionPayload | None:
    """Resolve a selection payload from a typed surface view."""
    if surface_view is None or not surface_view.items:
        return None
    if index is not None and 0 <= index < len(surface_view.items):
        return surface_view.items[index].payload
    if label:
        normalized_label = _normalize_label(label)
        exact = [item.payload for item in surface_view.items if _normalize_label(item.label) == normalized_label]
        if len(exact) == 1:
            return exact[0]

        contains_label = [
            item.payload
            for item in surface_view.items
            if _normalize_label(item.label) and _normalize_label(item.label) in normalized_label
        ]
        if len(contains_label) == 1:
            return contains_label[0]

        token_matches: list[tuple[int, SelectionPayload]] = []
        candidate_tokens = set(_tokenize_label(normalized_label))
        for item in surface_view.items:
            item_tokens = set(_tokenize_label(item.label))
            overlap = candidate_tokens & item_tokens
            if overlap:
                token_matches.append((max(len(token) for token in overlap), item.payload))
        if len(token_matches) == 1:
            return token_matches[0][1]
        if len(token_matches) > 1:
            token_matches.sort(key=lambda entry: entry[0], reverse=True)
            if token_matches[0][0] > token_matches[1][0]:
                return token_matches[0][1]
    return None


def _normalize_label(value: str) -> str:
    return " ".join(re.sub(r"'s\b", "", value.casefold()).split()).rstrip(".,;:!?")


def _tokenize_label(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) >= 3]
