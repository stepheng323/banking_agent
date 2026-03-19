"""Planner query-continuation shortcut helpers."""

from apps.core.src.agent.orchestrator.models.domain import TaskSpec
from apps.core.src.agent.orchestrator.services.query_shortcuts import (
    QueryShortcutDecision,
    resolve_query_shortcut,
    resolve_query_shortcut_with_reason,
)


def _next_query_continuation_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = f"query_continuation_{idx}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"query_continuation_{idx}"
    return task_id


__all__ = [
    "_next_query_continuation_task_id",
    "QueryShortcutDecision",
    "resolve_query_shortcut",
    "resolve_query_shortcut_with_reason",
]
