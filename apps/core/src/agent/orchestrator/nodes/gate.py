"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements deterministic routing for active sessions and query continuation.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _interrupt_router_context(state: OrchestratorState, session_domain: str) -> str:
    """Build compact context for pending-input routing."""
    interrupt = state.pending_interrupt
    if not interrupt:
        return f"active_domain={session_domain}"
    return (
        f"active_domain={session_domain}\n"
        f"interrupt_kind={interrupt.kind}\n"
        f"task_ids={interrupt.task_ids}\n"
        f"required_fields={interrupt.fields_by_task}\n"
        f"prompt={interrupt.prompt or ''}"
    )


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Fast Path Gate.

    1. Check for Active Sessions (Input Interrupt).
    2. Check for Query Continuation.
    3. Fallback to Planner (LLM-first for conversational/meta routing).
    """

    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    # --- 1. Active Session Input Handling ---
    if state.pending_interrupt and state.pending_interrupt.kind == "input":
        if session and session.state == "WAITING_FOR_INPUT":
            domain_allowlist = {"transfer", "airtime", "data", "support"}
            if session.domain in domain_allowlist:
                if state.last_message_text:
                    # Check for cancel/abort BEFORE treating as slot-filling
                    cancel_words = {"cancel", "abort", "stop", "nevermind", "never mind"}
                    msg_lower = state.last_message_text.strip().lower()
                    if msg_lower in cancel_words:
                        logger.info("fast_path_cancel_detected", domain=session.domain, input=msg_lower)
                        locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
                        return {
                            "pending_interrupt": None,
                            "tasks": {},
                            "waves": [],
                            "fast_path_triggered": True,
                            "final_response": render_message("planner.cancelled", locale),
                        }

                    task_planner = config["configurable"].get("task_planner")
                    if not task_planner or not hasattr(task_planner, "route_pending_input"):
                        logger.info(
                            "interrupt_router_fallback_planner",
                            domain=session.domain,
                            reason="router_unavailable",
                        )
                        return {}

                    try:
                        route_context = _interrupt_router_context(state, session.domain)
                        logger.info("interrupt_router_called", active_domain=session.domain)
                        route = await task_planner.route_pending_input(
                            state.phone_number,
                            state.last_message_text,
                            context=route_context,
                        )
                        logger.info(
                            "interrupt_router_decision",
                            decision=route.decision,
                            confidence=route.confidence,
                            active_domain=session.domain,
                            detected_language=route.detected_language,
                            target_intent=route.target_intent,
                        )
                    except Exception as e:
                        logger.warning(
                            "interrupt_router_fallback_planner",
                            domain=session.domain,
                            reason="router_failed",
                            error=str(e),
                        )
                        return {}

                    if route.decision != "continue_flow":
                        logger.info("fast_path_defer_to_planner", domain=session.domain, reason=route.decision)
                        return {}

                    new_tasks = state.tasks.copy()
                    updates = {
                        "pending_interrupt": None,
                        "last_interrupt": state.pending_interrupt,
                        "tasks": new_tasks,
                        "fast_path_triggered": True,
                    }
                    reset_tasks_to_extracted(
                        new_tasks,
                        state.pending_interrupt.task_ids,
                        copy_task=True,
                        clear_idempotency=False,
                    )

                    logger.info("fast_path_input_match", domain=session.domain, tasks=state.pending_interrupt.task_ids)
                    for tid, t in new_tasks.items():
                        logger.info("gate_task_debug", tid=tid, skip_ext=t.payload.get("skip_extraction"))
                    return updates

    message_text = (state.last_message_text or "").strip()

    if not state.pending_interrupt and session:
        message_lowered = message_text.lower()
        logger.info("gate_tier0_check", domain=session.domain, input_fragment=message_lowered[:20])

        # --- 3. Fast Query Resume ---
        if session.domain == "query":
            fast_keywords = {
                "more",
                "next",
                "back",
                "previous",
                "prev",
                "show",
                "filter",
                "sort",
                "details",
                "first",
                "last",
                "latest",
                "oldest",
                "drill",
                "expand",
            }

            first_word = message_lowered.split()[0] if message_lowered else ""
            is_fast_match = first_word in fast_keywords or "page" in message_lowered or "only" in message_lowered

            if is_fast_match:
                logger.info("fast_path_query_match", phrase=first_word)

                task_id = "fast_query_resume"
                spec = TaskSpec(
                    id=task_id,
                    type="query",
                    stage=TaskStage.DRAFT,
                    payload={
                        "message": state.last_message_text,
                        "is_fast_path": True,
                    },
                )

                return {
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "fast_path_triggered": True,
                }

    # --- PIN callback with no active session (checkpoint was cleaned) ---
    if state.pin_verified and not state.pending_interrupt:
        locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
        logger.warning("gate_pin_verified_no_session", reason="checkpoint_cleaned")
        return {
            "fast_path_triggered": True,
            "final_response": render_message("orchestrator.session.expired_pin", locale,
                                              fallback_en="Your transaction session has expired. Please start a new transaction."),
        }

    logger.info("gate_fallback_to_planner", reason="no_fast_path_match")
    return {}  # Fallback to planner logic
