"""Payload override assembly for semantic pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_payload_fields import (
    _pending_action_fields,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_payload_patch_router import (
    _pending_edit_patch_for_field,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_targets import (
    _pending_edit_target_task_ids,
)


def _pending_edit_payload_overrides_from_fields(
    *,
    state: OrchestratorState,
    interrupt: Any,
    target: Any,
    fields: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}

    overrides: dict[str, dict[str, Any]] = {}
    for field, value in fields.items():
        if value in (None, ""):
            continue
        target_ids = _pending_edit_target_task_ids(
            state=state,
            interrupt=interrupt,
            decision=target,
            field=str(field),
        )
        for task_id in target_ids:
            task = state.tasks.get(task_id)
            if task is None:
                continue
            patch = _pending_edit_patch_for_field(
                state=state,
                task=task,
                field=field,
                value=value,
            )
            if patch:
                overrides[task_id] = {**overrides.get(task_id, {}), **patch}

    return overrides


def _pending_edit_payload_overrides_from_decision(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> dict[str, dict[str, Any]]:
    overrides = _pending_edit_payload_overrides_from_fields(
        state=state,
        interrupt=interrupt,
        target=decision,
        fields=_pending_action_fields(getattr(decision, "fields", None)),
    )

    for scoped_update in getattr(decision, "updates", []) or []:
        scoped_overrides = _pending_edit_payload_overrides_from_fields(
            state=state,
            interrupt=interrupt,
            target=scoped_update,
            fields=_pending_action_fields(getattr(scoped_update, "fields", None)),
        )
        for task_id, patch in scoped_overrides.items():
            overrides[task_id] = {**overrides.get(task_id, {}), **patch}

    return overrides


__all__ = [
    "_pending_edit_payload_overrides_from_decision",
    "_pending_edit_payload_overrides_from_fields",
]
