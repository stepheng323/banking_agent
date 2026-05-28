"""Execution update construction for quoted replay tasks."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_modifiers import (
    _quoted_replay_source_override,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_payload_sanitize import (
    _sanitize_replay_task_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_payload_validation import (
    _format_missing_replay_fields,
    _replay_payload_missing_fields,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_scope import (
    _scope_requested,
    _select_seed_tasks,
)
from shared.types.planner import ContextFrameReplayModifier
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _next_quoted_replay_task_id(state: OrchestratorState, task_type: str, existing_ids: set[str] | None = None) -> str:
    seen = set(existing_ids or set())
    seen.update(state.tasks.keys())
    idx = 1
    task_id = f"quoted_replay_{task_type}_{idx}"
    while task_id in seen:
        idx += 1
        task_id = f"quoted_replay_{task_type}_{idx}"
    return task_id


def _build_quoted_replay_execution_updates(
    *,
    state: OrchestratorState,
    text: str,
    interpretation: QuotedReplayInterpretation,
    locale_updates: dict[str, Any],
    quoted_payload: dict[str, Any] | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> dict[str, Any] | None:
    new_tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    missing_fields: list[str] = []
    source_override_requested, source_override, source_reference = _quoted_replay_source_override(
        state,
        text,
        replay_modifier,
    )
    if source_override_requested and source_override is None:
        return {
            "final_response": (
                f"I could not find '{source_reference}' among your linked source accounts. "
                "Choose one of your linked accounts and try again."
            ),
            "normalized_instruction": text,
            "semantic_path_shape": "quoted_router",
            **locale_updates,
        }

    seed_tasks = _select_seed_tasks(quoted_payload=quoted_payload, interpretation=interpretation)
    use_seed_tasks = bool(seed_tasks and (_scope_requested(interpretation) or not interpretation.tasks))
    task_inputs: list[tuple[str, dict[str, Any]]] = []
    if use_seed_tasks:
        task_inputs = [
            (str(seed_task.get("task_type") or "").strip().lower(), dict(seed_task))
            for seed_task in seed_tasks
        ]
    else:
        task_inputs = [
            (item.task_type, item.payload.model_dump(exclude_none=True))
            for item in interpretation.tasks
        ]

    for task_type, payload in task_inputs:
        sanitized_payload = _sanitize_replay_task_payload(
            task_type=task_type,
            payload=payload,
            text=text,
            replay_modifier=replay_modifier,
            source_override=source_override,
        )
        if sanitized_payload is None:
            preview_payload = {key: value for key, value in payload.items() if value is not None}
            if task_type == "transfer" and not preview_payload.get("recipient_account"):
                recipient_account_number = preview_payload.get("recipient_account_number")
                if recipient_account_number:
                    preview_payload["recipient_account"] = recipient_account_number
            missing_fields.extend(_replay_payload_missing_fields(task_type, preview_payload))
            continue
        task_id = _next_quoted_replay_task_id(state, task_type, allocated_ids)
        allocated_ids.add(task_id)
        new_tasks[task_id] = TaskSpec(
            id=task_id,
            type=cast(Any, task_type),
            stage=TaskStage.DRAFT,
            payload=sanitized_payload,
        )
        wave_ids.append(task_id)

    if not wave_ids:
        if missing_fields:
            return {
                "final_response": _format_missing_replay_fields(missing_fields),
                "normalized_instruction": text,
                "semantic_path_shape": "quoted_router",
                **locale_updates,
            }
        return None

    logger.info(
        "quoted_replay_shortcut_hit",
        decision=interpretation.decision,
        task_count=len(wave_ids),
    )
    return {
        "tasks": new_tasks,
        "waves": [wave_ids],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "semantic_path_shape": "quoted_router",
        **locale_updates,
    }


__all__ = [
    "_build_quoted_replay_execution_updates",
    "_next_quoted_replay_task_id",
]
