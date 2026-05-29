from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_edit_context import (
    build_pending_action_edit_context,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_edit_scope import (
    _is_supported_input_edit_interrupt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_edit_types import (
    PENDING_ACTION_EDIT_MIN_CONFIDENCE,
    PendingActionEditResolution,
)


class PendingActionEditEngine:
    """Classify pending confirmation edits and prepare deterministic resolution hints."""

    async def interpret(
        self,
        *,
        state: OrchestratorState,
        interrupt: Any,
        text: str,
        task_planner: TaskPlanner | None,
    ) -> PendingActionEditResolution | None:
        interrupt_kind = getattr(interrupt, "kind", None)
        if interrupt_kind != "confirmation" and not _is_supported_input_edit_interrupt(state, interrupt):
            return None
        if task_planner is None:
            return None

        context = build_pending_action_edit_context(state, interrupt)
        decision = await task_planner.interpret_pending_action_edit(
            state.phone_number,
            text,
            context=context,
            path_label="interrupt_path",
        )
        if decision.confidence < PENDING_ACTION_EDIT_MIN_CONFIDENCE:
            return None
        return PendingActionEditResolution(decision=decision)


__all__ = ["PendingActionEditEngine"]
