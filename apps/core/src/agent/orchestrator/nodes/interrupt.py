from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict:
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

        is_confirmation = False
        is_cancellation = False

        if state.pin_verified:
            is_confirmation = True
        else:
            task_planner = config["configurable"].get("task_planner")
            if task_planner:
                try:
                    context_summary = f"Active Flow: Confirmation for tasks {interrupt.task_ids}"
                    planner_output = await task_planner.plan_tasks(
                        state.phone_number, state.last_message_text or "", context=context_summary
                    )
                    is_confirmation = getattr(planner_output, "is_confirmation", False)
                    is_cancellation = getattr(planner_output, "is_cancellation", False)
                    logger.info("confirmation_intent_detected", is_conf=is_confirmation, is_canc=is_cancellation)
                except Exception as e:
                    logger.error("confirmation_planner_failed", error=str(e))
                    # Fallback to simple keyword check if LLM fails
                    is_confirmation = any(w in text for w in ["confirm", "yes", "ok", "proceed"])
                    is_cancellation = any(w in text for w in ["cancel", "stop"])

        if is_confirmation:
            logger.info("confirmation_confirmed", tasks=interrupt.task_ids, via_pin=state.pin_verified)
            for tid in interrupt.task_ids:
                task = new_tasks[tid].model_copy(deep=True)
                task.payload["confirmation"]["confirmed"] = True
                task.stage = TaskStage.EXECUTING if state.pin_verified else TaskStage.AWAITING_AUTH
                new_tasks[tid] = task
            return {
                "pending_interrupt": None,
                "tasks": new_tasks,
            }

        elif is_cancellation:
            logger.info("confirmation_cancelled", tasks=interrupt.task_ids)
            for tid in interrupt.task_ids:
                task = new_tasks[tid].model_copy(deep=True)
                task.stage = TaskStage.CANCELLED
                new_tasks[tid] = task

            return {
                "pending_interrupt": None,
                "tasks": new_tasks,
            }

        else:
            logger.info("confirmation_interrupt_input_mismatch", text=text)
            for tid in interrupt.task_ids:
                task = new_tasks[tid].model_copy(deep=True)
                task.stage = TaskStage.EXTRACTED
                task.payload["confirmation"] = {}
                new_tasks[tid] = task

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
