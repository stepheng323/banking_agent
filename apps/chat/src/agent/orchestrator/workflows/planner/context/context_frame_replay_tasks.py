"""Task construction for context-frame transaction replay."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextEntity
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_accounts import (
    _loaded_accounts,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_narration import (
    _replay_narration_override,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_payload_source import (
    enrich_replay_source_account,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_payload_transactions import (
    replay_payload_for_entity,
)
from shared.types.planner import ContextFrameReplayModifier


def _new_replay_task_id(state: OrchestratorState, task_type: str, allocated_ids: set[str]) -> str:
    seen = set(state.tasks.keys()) | allocated_ids
    idx = 1
    task_id = f"context_replay_{task_type}_{idx}"
    while task_id in seen:
        idx += 1
        task_id = f"context_replay_{task_type}_{idx}"
    return task_id


def _apply_replay_narration_override(
    payload: dict[str, Any],
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> None:
    narration = _replay_narration_override(
        text,
        accounts=_loaded_accounts(state),
        replay_modifier=replay_modifier,
    )
    if not narration:
        return

    payload["narration"] = narration
    payload["authored_narration"] = narration
    payload["user_note"] = narration


def build_context_frame_replay_tasks(
    *,
    state: OrchestratorState,
    entities: list[ContextEntity],
    text: str,
    source_patch: dict[str, Any] | None,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[dict[str, TaskSpec], list[str]]:
    tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    for entity in entities:
        replay_payload = replay_payload_for_entity(entity, text=text, replay_modifier=replay_modifier)
        if replay_payload is None:
            continue
        task_type, payload = replay_payload
        enrich_replay_source_account(payload, state)
        if source_patch is not None:
            payload.update(source_patch)
        if task_type == "transfer":
            _apply_replay_narration_override(payload, text, state, replay_modifier)
        task_id = _new_replay_task_id(state, task_type, allocated_ids)
        allocated_ids.add(task_id)
        tasks[task_id] = TaskSpec(id=task_id, type=cast(Any, task_type), stage=TaskStage.DRAFT, payload=payload)
        wave_ids.append(task_id)
    return tasks, wave_ids


__all__ = ["build_context_frame_replay_tasks"]
