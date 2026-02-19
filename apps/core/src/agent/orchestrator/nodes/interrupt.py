from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.utils.task_payload import build_task_spec_from_plan_item
from apps.core.src.agent.orchestrator.utils.task_state import (
    reset_tasks_to_extracted,
    set_tasks_cancelled,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _stash_current_session(
    state: OrchestratorState,
    *,
    interrupt: Any,
    intent: str,
) -> list[dict[str, Any]]:
    current_session = {
        "tasks": state.tasks,
        "waves": state.waves,
        "current_wave_index": state.current_wave_index,
        "pending_interrupt": interrupt,
        "intent": intent,
    }
    return state.stashed_sessions + [current_session]


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
                    set_tasks_cancelled(state.tasks, interrupt.task_ids, copy_task=True)
                    return {"pending_interrupt": None, "last_interrupt": interrupt, "tasks": state.tasks}

                # Check if we should replan
                should_replan = False

                if planner_output.tasks and new_task_types != current_task_types:
                    should_replan = True

                    # [Guard] if we are in a Transfer flow, be very skeptical of switching to
                    # 'account', 'beneficiary', or 'query' unless explicit cancellation.
                    if "transfer" in current_task_types:
                        suspicious = any(
                            t.executor in ("account", "beneficiary", "query") for t in planner_output.tasks
                        )
                        if suspicious and not getattr(planner_output, "is_cancellation", False):
                            logger.warning(
                                "blocking_context_switch",
                                reason="active_transfer_protected",
                                attempted_types=sorted(new_task_types),
                                input_text=text,
                            )
                            should_replan = False

                        # [Stability] Sticky Transfer: If Transfer -> Transfer, prefer Update (Extraction) over Replan.
                        # This prevents data entry (e.g. "Opay") from incorrectly being seen as a new single task,
                        # which would wipe out other parallel tasks (e.g. Tolu).
                        elif "transfer" in new_task_types and not getattr(planner_output, "is_cancellation", False):
                            logger.info("enforcing_sticky_transfer", reason="prevent_replan_wipe")
                            should_replan = False

                if should_replan:
                    new_tasks = {}
                    wave_tasks: list[str] = []

                    # Stash current session
                    active_type = next(iter(current_task_types)) if current_task_types else "unknown"
                    stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)

                    for plan_item in planner_output.tasks:
                        spec = build_task_spec_from_plan_item(
                            plan_item,
                            text,
                            preserve_existing_action_instruction=False,
                            include_skip_extraction=False,
                            strip_transfer_recipient_suffix=False,
                            format_narration_requires_recipient_field=True,
                        )
                        new_tasks[spec.id] = spec
                        wave_tasks.append(spec.id)

                    logger.info("input_interrupt_replanned", from_types=sorted(current_task_types))
                    return {
                        "pending_interrupt": None,
                        "last_interrupt": interrupt,
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

        reset_tasks_to_extracted(
            state.tasks,
            interrupt.task_ids,
            copy_task=False,
            clear_idempotency=True,
        )

    elif interrupt.kind == "confirmation":
        text = (state.last_message_text or "").lower()
        new_tasks = state.tasks.copy()

        planner_output = None
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
                "last_interrupt": interrupt,
                "tasks": new_tasks,
            }

        elif is_cancellation:
            logger.info("confirmation_cancelled", tasks=interrupt.task_ids)
            set_tasks_cancelled(new_tasks, interrupt.task_ids, copy_task=True)

            return {
                "pending_interrupt": None,
                "last_interrupt": interrupt,
                "tasks": new_tasks,
            }

        active_type = (
            state.tasks[interrupt.task_ids[0]].type
            if interrupt.task_ids and interrupt.task_ids[0] in state.tasks
            else "unknown"
        )
        if (
            planner_output
            and planner_output.tasks
            and planner_output.primary_intent not in (active_type, "conversational", "mixed")
        ):
            logger.info("confirmation_intent_switch", old=active_type, new=planner_output.primary_intent)
            # Replan Logic (similar to input interrupt)
            new_tasks_map = {}
            wave_tasks: list[str] = []

            # Stash current session
            stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)

            for plan_item in planner_output.tasks:
                spec = build_task_spec_from_plan_item(
                    plan_item,
                    text,
                    preserve_existing_action_instruction=False,
                    include_skip_extraction=False,
                    strip_transfer_recipient_suffix=False,
                    format_narration_requires_recipient_field=True,
                )
                new_tasks_map[spec.id] = spec
                wave_tasks.append(spec.id)

            return {
                "pending_interrupt": None,
                "last_interrupt": interrupt,
                "tasks": new_tasks_map,
                "waves": [wave_tasks],
                "current_wave_index": 0,
                "planner_output": planner_output,
                "stashed_sessions": stashed,
            }

        else:
            logger.info("confirmation_interrupt_input_mismatch", text=text)
            reset_tasks_to_extracted(
                new_tasks,
                interrupt.task_ids,
                copy_task=True,
                clear_idempotency=False,
            )

            return {
                "pending_interrupt": None,
                "last_interrupt": interrupt,
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
                set_tasks_cancelled(state.tasks, interrupt.task_ids, copy_task=True)
            else:
                # If we're here and PIN isn't verified, the user likely typed text.
                # Reset tasks to EXTRACTED so the worker can re-process the input.
                reset_tasks_to_extracted(
                    state.tasks,
                    interrupt.task_ids,
                    copy_task=True,
                    clear_idempotency=False,
                )

    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
    }
