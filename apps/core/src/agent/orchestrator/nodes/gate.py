"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements deterministic routing for active sessions and query continuation.
"""

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.i18n import LocaleManager, render_locale_switched, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
TURN_ROUTER_MAX_WORDS = 6
TURN_ROUTER_MAX_CHARS = 64
TURN_ROUTER_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")


def _next_fast_query_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "fast_query_resume"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"fast_query_resume_{idx}"
    return task_id


def _locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _should_invoke_turn_router(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    if any(marker in normalized for marker in TURN_ROUTER_MULTI_CLAUSE_MARKERS):
        return True
    if any(char.isdigit() for char in normalized):
        return False
    return len(normalized) <= TURN_ROUTER_MAX_CHARS and len(normalized.split()) <= TURN_ROUTER_MAX_WORDS


def _build_turn_router_context(state: OrchestratorState, session_domain: str | None) -> str:
    active_domain = state.active_domain or "none"
    session_part = session_domain or "none"
    expected = state.preplanner_expected_transaction_executors or []
    return (
        f"active_domain={active_domain}; "
        f"session_domain={session_part}; "
        f"expected_transaction_executors={','.join(expected) if expected else 'none'}"
    )


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Fast Path Gate.

    1. Check for Active Sessions (Input Interrupt).
    2. Check for Query Continuation.
    3. Fallback to Planner (LLM-first for conversational/meta routing).
    """

    task_planner = config["configurable"].get("task_planner")
    redis_client = config["configurable"].get("redis_client")
    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    message_text = (state.last_message_text or "").strip()

    if not state.pending_interrupt:
        explicit_locale = LocaleManager.parse_explicit_switch_command(message_text)
        if explicit_locale:
            if redis_client:
                resolved = await LocaleManager.set_locale(state.phone_number, explicit_locale, source="user_command")
                next_locale = resolved.value
            else:
                next_locale = explicit_locale.value
            logger.info("gate_locale_switch_fastpath", locale=next_locale)
            return {
                "fast_path_triggered": True,
                "final_response": render_locale_switched(next_locale),
                **_locale_update(state, next_locale),
            }

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

                task_id = _next_fast_query_task_id(state.tasks)
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
            "final_response": render_message(
                "orchestrator.session.expired_pin",
                locale,
                fallback_en="Your transaction session has expired. Please start a new transaction.",
            ),
        }

    if (
        not state.pending_interrupt
        and not state.has_quote
        and callable(getattr(task_planner, "route_turn", None))
        and _should_invoke_turn_router(message_text)
    ):
        try:
            route = await task_planner.route_turn(
                state.phone_number,
                message_text,
                context=_build_turn_router_context(state, session.domain if session else None),
            )
        except Exception as exc:
            logger.warning("gate_turn_router_failed", error=str(exc))
            route = None

        if route is not None:
            expected_executors = [
                str(item)
                for item in (getattr(route, "expected_transaction_executors", None) or [])
                if str(item) in TRANSACTION_EXECUTORS
            ]
            updates: dict[str, Any] = {}
            if expected_executors:
                updates["preplanner_expected_transaction_executors"] = expected_executors

            if route.decision == "respond_directly":
                locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
                if route.detected_language:
                    locale = LocaleManager.from_detection(route.detected_language).value
                    updates.update(_locale_update(state, locale))
                if route.response_key:
                    text = render_message(route.response_key, locale)
                else:
                    text = route.response or render_message("conversational.clarify", locale)
                logger.info("gate_turn_router_direct_response", response_key=route.response_key, locale=locale)
                return {
                    "fast_path_triggered": True,
                    "final_response": text,
                    **updates,
                }

            if route.decision == "query_continuation":
                task_id = _next_fast_query_task_id(state.tasks)
                spec = TaskSpec(
                    id=task_id,
                    type="query",
                    stage=TaskStage.DRAFT,
                    payload={
                        "message": state.last_message_text,
                        "is_fast_path": True,
                    },
                )
                logger.info("gate_turn_router_query_continuation", task_id=task_id)
                return {
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "fast_path_triggered": True,
                    **updates,
                }

            if updates:
                logger.info("gate_turn_router_expected_executors", executors=expected_executors)
                return updates

    logger.info("gate_fallback_to_planner", reason="no_fast_path_match")
    return {}  # Fallback to planner logic
