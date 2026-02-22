"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements deterministic routing for active sessions and query continuation.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Fast Path Gate.

    1. Check for Active Sessions (Input Interrupt).
    2. Check for Query Continuation.
    3. Fallback to Planner (LLM-first for conversational/meta routing).
    """

    del config
    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

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

    logger.info("gate_fallback_to_planner", reason="no_fast_path_match")
    return {}  # Fallback to planner logic
