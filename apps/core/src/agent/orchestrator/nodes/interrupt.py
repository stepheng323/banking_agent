from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_pending_interrupt(state: OrchestratorState) -> dict:
    """Process user input against the pending interrupt (if any)."""
    interrupt = state.pending_interrupt
    if not interrupt:
        return {}

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)

    if interrupt.kind == "input":
        for tid in interrupt.task_ids:
            task = state.tasks[tid]
            task.stage = TaskStage.EXTRACTED
            task.payload["confirmation"] = {}
            task.payload.pop("idempotency_key", None)

    elif interrupt.kind == "confirmation":
        text = (state.last_message_text or "").lower()
        new_tasks = state.tasks.copy()

        if state.pin_verified:
            logger.info("confirmation_via_pin", tasks=interrupt.task_ids)
            for tid in interrupt.task_ids:
                task = new_tasks[tid].model_copy(deep=True)
                task.payload["confirmation"]["confirmed"] = True
                # Skip AWAITING_AUTH since we already have the PIN
                task.stage = TaskStage.EXECUTING
                new_tasks[tid] = task
        elif "confirm" in text or "yes" in text or "ok" in text or "proceed" in text:
            for tid in interrupt.task_ids:
                task = new_tasks[tid].model_copy(deep=True)
                task.payload["confirmation"]["confirmed"] = True
                task.stage = TaskStage.AWAITING_AUTH
                new_tasks[tid] = task
        else:
            # Re-extraction logic (unchanged)
            pass

        return {
            "pending_interrupt": None,
            "tasks": new_tasks,
        }

    elif interrupt.kind == "auth":
        if state.pin_verified:
            for tid in interrupt.task_ids:
                task = state.tasks[tid]
                task.stage = TaskStage.EXECUTING
        else:
            # Retry logic handled by re-emitting interrupt if next pass fails
            pass

    return {
        "pending_interrupt": None,
        "tasks": state.tasks,  # Persist updates
    }
