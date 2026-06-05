"""Schedule edit/cancel task creation for context-frame follow-ups."""

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import derive_transfer_schedule_fields
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE,
    canonical_decision,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from shared.types.planner import ContextFrameFollowupDecision


@dataclass(frozen=True, slots=True)
class ScheduleManagementResult:
    recent_domain_focus: str = "schedule"
    tasks: dict[str, TaskSpec] | None = None
    waves: list[list[str]] | None = None


def build_schedule_management_result(
    state: OrchestratorState,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
) -> ScheduleManagementResult | None:
    return build_schedule_management_result_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        text,
    )


def build_schedule_management_result_for_view(
    state_view: ContextFrameStateView,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
) -> ScheduleManagementResult | None:
    semantic_decision = canonical_decision(decision.decision)
    if semantic_decision not in {"edit_schedule", "cancel_schedule"}:
        return None
    if (
        frame.frame_type != ContextFrameType.SCHEDULE_LIST
        or decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE
    ):
        return None

    selected = _schedule_entity_for_management(frame, decision)
    data = selected.data if selected is not None and isinstance(selected.data, dict) else {}
    schedule_id = str(data.get("schedule_id") or selected.entity_id or "").strip() if selected is not None else ""
    action = "edit_scheduled_transaction" if semantic_decision == "edit_schedule" else "cancel_scheduled_transaction"
    payload: dict[str, Any] = {
        "action": action,
        "message": text,
        "instruction": text,
    }
    if schedule_id:
        payload["schedule_selector"] = schedule_id
        payload["schedule_id"] = schedule_id

    if semantic_decision == "edit_schedule":
        schedule_fields = derive_transfer_schedule_fields(
            text,
            schedule_text=None,
            scheduled_text=None,
            recurring_flag=None,
        )
        payload.update(schedule_fields)

    task_id = _new_schedule_management_task_id_for_view(state_view)
    task = TaskSpec(
        id=task_id,
        type="schedule",
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return ScheduleManagementResult(tasks={task_id: task}, waves=[[task_id]])


def _new_schedule_management_task_id(state: OrchestratorState, allocated_ids: set[str] | None = None) -> str:
    return _new_schedule_management_task_id_for_view(context_frame_state_view(state), allocated_ids)


def _new_schedule_management_task_id_for_view(
    state_view: ContextFrameStateView,
    allocated_ids: set[str] | None = None,
) -> str:
    seen = state_view.task_ids | set(allocated_ids or set())
    idx = 1
    task_id = "context_schedule_management_1"
    while task_id in seen:
        idx += 1
        task_id = f"context_schedule_management_{idx}"
    return task_id


def _schedule_entity_for_management(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> ContextEntity | None:
    if frame.frame_type != ContextFrameType.SCHEDULE_LIST:
        return None
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return frame.items[idx]
    target_text = decision_target_text(decision)
    if target_text:
        matches = find_matching_entities(frame, target_text)
        if len(matches) == 1:
            return matches[0]
    if len(frame.items) == 1:
        return frame.items[0]
    return None


__all__ = ["ScheduleManagementResult", "build_schedule_management_result", "build_schedule_management_result_for_view"]
