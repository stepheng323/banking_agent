"""Task selection for continue-flow confirmation updates."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_target_matching import (
    _message_targets_confirmation_task,
)

_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)


def _select_confirmation_continue_flow_task_ids(
    state: OrchestratorState,
    interrupt: Any,
) -> tuple[list[str], str, list[str]]:
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(task_ids) < 2:
        return task_ids, "single_or_empty_batch", []

    message_text = (state.last_message_text or "").strip()
    if not message_text:
        return task_ids, "empty_message", []

    if _CONFIRMATION_COLLECTIVE_SCOPE_RE.search(message_text):
        return task_ids, "collective_scope", []

    matched_task_ids = [
        task_id
        for task_id in task_ids
        if _message_targets_confirmation_task(message_text, state.tasks[task_id])
    ]
    if 0 < len(matched_task_ids) < len(task_ids):
        return matched_task_ids, "matched_subset", matched_task_ids
    if not matched_task_ids:
        return task_ids, "no_recipient_match", []
    return task_ids, "matched_all", matched_task_ids


__all__ = [
    "_select_confirmation_continue_flow_task_ids",
]
