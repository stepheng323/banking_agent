"""User-message selection for execution task handlers."""

from __future__ import annotations

from typing import cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import pop_task_payload_value
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _maybe_user_message(task: TaskSpec, state: OrchestratorState) -> str | None:
    logger.info(
        "maybe_user_msg_check",
        task_id=task.id,
        last_int=state.last_interrupt.task_ids if state.last_interrupt else None,
    )
    if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
        # Only tasks that asked for input consume the current user text.
        if state.last_interrupt and task.id not in state.last_interrupt.task_ids:
            return None
        scoped_user_message = pop_task_payload_value(task, "pending_user_message")
        if isinstance(scoped_user_message, str) and scoped_user_message.strip():
            return scoped_user_message
        return cast(str | None, state.last_message_text)
    return None


__all__ = ["_maybe_user_message"]
