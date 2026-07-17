from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import (
    CONTEXT_READ_LIST_LIMIT,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_selection import (
    active_context_frames_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import SEARCHABLE_DATA_KEYS
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)


def build_context_frame_followup_context(frame: ContextFrame) -> str:
    """Build follow-up interpreter context for one frame."""
    lines = [f"Frame type: {frame.frame_type.value}", f"Item count: {len(frame.items)}", "Items:"]
    read_request = frame.metadata.get("read_request")
    if isinstance(read_request, dict):
        read_fields = []
        for key in (
            "subject",
            "response_shape",
            "entity_name",
            "bank_name",
            "status",
            "reference",
            "offset",
            "page_size",
        ):
            value = read_request.get(key)
            if value is not None and value != "":
                read_fields.append(f"{key}={value}")
        lines.insert(2, f"Canonical read: {'; '.join(read_fields)}")
        lines.insert(3, f"Total matches: {frame.metadata.get('total_count', 0)}")
    balance_contract = frame.metadata.get("balance_contract")
    balance_state = frame.metadata.get("balance_conversation_state")
    if isinstance(balance_contract, dict):
        lines.insert(
            4,
            "Balance contract: "
            f"scope={balance_contract.get('account_scope')}; "
            f"banks={balance_contract.get('bank_names', [])}; "
            f"operation={balance_contract.get('operation')}",
        )
    if isinstance(balance_state, dict):
        lines.insert(
            5,
            "Balance conversation: "
            f"focus={balance_state.get('focused_bank')}; "
            f"mentioned={balance_state.get('mentioned_banks', [])}; "
            f"last_result={balance_state.get('last_result_banks', [])}",
        )
    for key, label in (
        ("beneficiary_contract", "Beneficiary set contract"),
        ("schedule_contract", "Schedule set contract"),
        ("account_lifecycle_contract", "Account lifecycle contract"),
    ):
        contract = frame.metadata.get(key)
        if isinstance(contract, dict):
            lines.insert(4, f"{label}: {contract}")
    set_state = frame.metadata.get("conversation_set_state")
    if isinstance(set_state, dict):
        lines.insert(
            5,
            "Conversation set: "
            f"domain={set_state.get('domain')}; "
            f"mentioned_count={len(set_state.get('mentioned_refs', []))}; "
            f"last_result_count={len(set_state.get('last_result_refs', []))}",
        )
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
