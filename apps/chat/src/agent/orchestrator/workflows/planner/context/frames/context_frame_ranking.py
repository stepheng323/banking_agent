"""Ranking helpers for context-frame entities."""

import re
from datetime import datetime

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import semantic_tokens

_RANKING_ALIASES = {
    "largest": "max",
    "highest": "max",
    "biggest": "max",
    "most": "max",
    "smallest": "min",
    "lowest": "min",
    "least": "min",
    "newest": "newest",
    "latest": "newest",
    "recent": "newest",
    "oldest": "oldest",
    "earliest": "oldest",
}


def numeric_rank_value(entity: ContextEntity) -> float | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in ("amount", "count", "balance", "available_balance"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = re.sub(r"[^0-9.\-]", "", value)
            if cleaned:
                try:
                    return float(cleaned)
                except ValueError:
                    continue
    return None


def _date_rank_value(entity: ContextEntity) -> float | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in ("date", "completed_at", "created_at"):
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            candidate = value.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(candidate).timestamp()
            except ValueError:
                continue
    return None


def ranked_entity(frame: ContextFrame, rank_text: str | None) -> ContextEntity | None:
    tokens = semantic_tokens(rank_text)
    rank_mode = next((_RANKING_ALIASES[token] for token in tokens if token in _RANKING_ALIASES), None)
    if rank_mode is None:
        return None

    if rank_mode in {"newest", "oldest"}:
        scored = [(entity, value) for entity in frame.items if (value := _date_rank_value(entity)) is not None]
        if not scored:
            return None
        if rank_mode == "newest":
            return max(scored, key=lambda item: item[1])[0]
        return min(scored, key=lambda item: item[1])[0]

    scored = [(entity, value) for entity in frame.items if (value := numeric_rank_value(entity)) is not None]
    if not scored:
        return None
    return max(scored, key=lambda item: item[1])[0] if rank_mode == "max" else min(scored, key=lambda item: item[1])[0]


__all__ = ["numeric_rank_value", "ranked_entity"]
