"""Confirmation update message normalization."""

import re

from banking.presentation.i18n.renderer import render_message


def _compact_confirmation_update_message(update_messages: list[str], locale: str) -> str | None:
    normalized: list[str] = []
    seen: set[str] = set()
    for message in update_messages:
        compact = message.strip()
        if not compact:
            continue
        key = re.sub(r"\s+", " ", compact).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(compact)

    if not normalized:
        return None
    if len(normalized) == 1:
        return normalized[0]
    return render_message(
        "response.templates.acknowledge_change",
        locale,
        {"changes_text": "your transfer details"},
    )


__all__ = ["_compact_confirmation_update_message"]
