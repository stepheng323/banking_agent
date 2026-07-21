"""Execution-level selection of canonical user-visible progress stages."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from banking.runtime.progress import operation_progress_spec
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def enter_task_progress(ctx: ExecutionTurnContext, task: TaskSpec) -> None:
    """Set the approved stage for an executing operation, if any.

    The registry deliberately excludes reads and non-actionable task states.
    It also supplies no customer data as stage metadata.
    """

    action = task.payload.get("action")
    if not isinstance(action, str):
        return
    spec = operation_progress_spec(task.type, action)
    if spec is None:
        return
    task_stage = getattr(task.stage, "value", task.stage)
    if spec.requires_executing_stage and task_stage != "executing":
        return
    tracker = ctx.dependencies.progress_tracker
    if tracker is None:
        return

    await tracker.set_stage(spec.stage_key)
    logger.info(
        "progress_stage_entered",
        domain=task.type,
        action=action,
        stage_key=spec.stage_key,
    )


__all__ = ["enter_task_progress"]
