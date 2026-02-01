from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict:
    """Process user input against the pending interrupt (if any)."""
    interrupt = state.pending_interrupt
    if not interrupt:
        return {}

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)

    if interrupt.kind == "input":
        text = state.last_message_text or ""
        current_task_types = {state.tasks[tid].type for tid in interrupt.task_ids if tid in state.tasks}
        task_planner = config["configurable"].get("task_planner")
        if task_planner and text:
            try:
                context_summary = (
                    "Active Flow: Input required for tasks "
                    f"{interrupt.task_ids} (types: {', '.join(sorted(current_task_types))})."
                )
                planner_output = await task_planner.plan_tasks(state.phone_number, text, context=context_summary)
                new_task_types = {t.executor for t in planner_output.tasks}
                logger.info(
                    "input_interrupt_intent_detected",
                    intent=planner_output.primary_intent,
                    confidence=planner_output.confidence,
                    new_task_types=sorted(new_task_types),
                )

                if getattr(planner_output, "is_cancellation", False):
                    for tid in interrupt.task_ids:
                        task = state.tasks[tid].model_copy(deep=True)
                        task.stage = TaskStage.CANCELLED
                        state.tasks[tid] = task
                    return {"pending_interrupt": None, "tasks": state.tasks}

                if planner_output.tasks and new_task_types != current_task_types:
                    new_tasks: dict[str, TaskSpec] = {}
                    wave_tasks: list[str] = []
                    
                    # Stash current session
                    active_type = next(iter(current_task_types)) if current_task_types else "unknown"
                    current_session = {
                        "tasks": state.tasks,
                        "waves": state.waves,
                        "current_wave_index": state.current_wave_index,
                        "pending_interrupt": interrupt,
                        "intent": active_type
                    }
                    stashed = state.stashed_sessions + [current_session]

                    for plan_item in planner_output.tasks:
                        payload = plan_item.parameters.model_dump() if plan_item.parameters else {}
                        if plan_item.action:
                            payload["action"] = plan_item.action
                        if plan_item.instruction:
                            payload["instruction"] = plan_item.instruction
                        
                        if plan_item.executor == "query" and not payload.get("message"):
                            payload["message"] = plan_item.instruction or text
                        spec = TaskSpec(
                            id=plan_item.task_id,
                            type=plan_item.executor,
                            stage=TaskStage.DRAFT,
                            payload=payload,
                        )
                        new_tasks[spec.id] = spec
                        wave_tasks.append(spec.id)

                    logger.info("input_interrupt_replanned", from_types=sorted(current_task_types))
                    return {
                        "pending_interrupt": None,
                        "tasks": new_tasks,
                        "waves": [wave_tasks],
                        "current_wave_index": 0,
                        "normalized_instruction": text,
                        "planner_output": planner_output,
                        "task_results": {},
                        "stashed_sessions": stashed,
                    }
            except Exception as e:
                logger.warning("input_interrupt_planner_failed", error=str(e))

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
                    is_confirmation = False
                    is_cancellation = False

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
            
        active_type = state.tasks[interrupt.task_ids[0]].type if interrupt.task_ids and interrupt.task_ids[0] in state.tasks else "unknown"
        if planner_output and planner_output.tasks and planner_output.primary_intent not in (active_type, "conversational", "mixed"):
             logger.info("confirmation_intent_switch", old=active_type, new=planner_output.primary_intent)
             # Replan Logic (similar to input interrupt)
             new_tasks_map: dict[str, TaskSpec] = {}
             wave_tasks: list[str] = []
             
             # Stash current session
             current_session = {
                 "tasks": state.tasks,
                 "waves": state.waves,
                 "current_wave_index": state.current_wave_index,
                 "pending_interrupt": interrupt, # The current confirmation interrupt
                 "intent": active_type 
             }
             stashed = state.stashed_sessions + [current_session]
             
             for plan_item in planner_output.tasks:
                 payload = plan_item.parameters.model_dump() if plan_item.parameters else {}
                 if plan_item.action:
                     payload["action"] = plan_item.action
                 if plan_item.instruction:
                     payload["instruction"] = plan_item.instruction
                 
                 if plan_item.executor == "query" and not payload.get("message"):
                     payload["message"] = plan_item.instruction or text
                 spec = TaskSpec(
                     id=plan_item.task_id,
                     type=plan_item.executor,
                     stage=TaskStage.DRAFT,
                     payload=payload,
                 )
                 new_tasks_map[spec.id] = spec
                 wave_tasks.append(spec.id)
                 
             return {
                 "pending_interrupt": None,
                 "tasks": new_tasks_map,
                 "waves": [wave_tasks],
                 "current_wave_index": 0,
                 "planner_output": planner_output,
                 "stashed_sessions": stashed,
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
            # Check if user wants to cancel
            is_cancellation = False
            task_planner = config["configurable"].get("task_planner")
            if task_planner and state.last_message_text:
                try:
                    context_summary = f"Active Flow: Auth for tasks {interrupt.task_ids}"
                    planner_output = await task_planner.plan_tasks(
                        state.phone_number, state.last_message_text, context=context_summary
                    )
                    is_cancellation = getattr(planner_output, "is_cancellation", False)
                    logger.info("auth_intent_detected", is_canc=is_cancellation)
                except Exception as e:
                    logger.error("auth_planner_failed", error=str(e))

            if is_cancellation:
                logger.info("auth_cancelled", tasks=interrupt.task_ids)
                for tid in interrupt.task_ids:
                    task = state.tasks[tid].model_copy(deep=True)
                    task.stage = TaskStage.CANCELLED
                    state.tasks[tid] = task
            else:
                # If we're here and PIN isn't verified, the user likely typed text.
                # Reset tasks to EXTRACTED so the worker can re-process the input.
                for tid in interrupt.task_ids:
                    task = state.tasks[tid].model_copy(deep=True)
                    task.stage = TaskStage.EXTRACTED
                    task.payload["confirmation"] = {}
                    state.tasks[tid] = task

    return {
        "pending_interrupt": None,
        "tasks": state.tasks,
    }
