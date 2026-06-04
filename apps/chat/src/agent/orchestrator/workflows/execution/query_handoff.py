"""Query-to-transfer handoff helpers."""

from __future__ import annotations

from typing import Any


def _next_query_handoff_transfer_task_id(tasks: dict[str, Any]) -> str:
    index = 1
    candidate = f"query_handoff_transfer_{index}"
    while candidate in tasks:
        index += 1
        candidate = f"query_handoff_transfer_{index}"
    return candidate


__all__ = ["_next_query_handoff_transfer_task_id"]
