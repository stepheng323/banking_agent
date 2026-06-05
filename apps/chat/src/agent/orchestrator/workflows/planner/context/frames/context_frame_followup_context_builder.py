from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_selection import (
    active_context_frames_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import SEARCHABLE_DATA_KEYS
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    CONTEXT_READ_LIST_LIMIT,
)


def build_context_frame_followup_context(frame: ContextFrame) -> str:
    """Build follow-up interpreter context for one frame."""
    lines = [f"Frame type: {frame.frame_type.value}", f"Item count: {len(frame.items)}", "Items:"]
    for idx, entity in enumerate(frame.items[:CONTEXT_READ_LIST_LIMIT], 1):
        data = entity.data if isinstance(entity.data, dict) else {}
        searchable_values = []
        for key in SEARCHABLE_DATA_KEYS:
            value = data.get(key)
            if value is not None and value != "":
                searchable_values.append(f"{key}={value}")
        suffix = f" | {'; '.join(searchable_values[:6])}" if searchable_values else ""
        lines.append(f"{idx}. {entity.label}{suffix}")
    overflow = len(frame.items) - CONTEXT_READ_LIST_LIMIT
    if overflow > 0:
        lines.append(f"... {overflow} more item(s) not shown in interpreter context")
    return "\n".join(lines)


def build_context_frame_followup_context_for_state(state: OrchestratorState) -> str:
    return build_context_frame_followup_context_for_state_view(context_frame_state_view(state))


def build_context_frame_followup_context_for_state_view(state_view: ContextFrameStateView) -> str:
    """Build follow-up interpreter context from current and related active frames."""
    frames = active_context_frames_for_view(state_view)
    if not frames:
        return ""
    if len(frames) == 1:
        return build_context_frame_followup_context(frames[-1])

    selected_frames = list(reversed(frames[-3:]))
    lines = [
        "Active displayed context, most recent first.",
        "Use the current focus for pronouns like this/that.",
        "Use an earlier list when the user refers to a visible item number not present in the current focus.",
    ]
    for idx, frame in enumerate(selected_frames):
        title = "Current focus" if idx == 0 else f"Earlier result {idx}"
        lines.append("")
        lines.append(f"{title}:")
        lines.append(build_context_frame_followup_context(frame))
    return "\n".join(lines)


__all__ = [
    "build_context_frame_followup_context",
    "build_context_frame_followup_context_for_state",
    "build_context_frame_followup_context_for_state_view",
]
