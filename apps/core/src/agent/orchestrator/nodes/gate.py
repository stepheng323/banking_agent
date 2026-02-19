"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements the Deterministic Routing Logic + Cached Meta.
"""

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.core.src.agent.orchestrator.models.domain import MetaIntent, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from shared.utils.logging import get_logger

logger = get_logger(__name__)


META_INTENT_MAP = {
    "who are you": MetaIntent.IDENTITY,
    "what are you": MetaIntent.IDENTITY,
    "your name": MetaIntent.IDENTITY,
    "who created you": MetaIntent.BRAND_ORIGIN,
    "who made you": MetaIntent.BRAND_ORIGIN,
    "who built you": MetaIntent.BRAND_ORIGIN,
    "who owns you": MetaIntent.BRAND_ORIGIN,
    "what can you do": MetaIntent.CAPABILITIES,
    "capabilities": MetaIntent.CAPABILITIES,
    "features": MetaIntent.CAPABILITIES,
    "help": MetaIntent.CAPABILITIES,
    "assist": MetaIntent.CAPABILITIES,
    "menu": MetaIntent.CAPABILITIES,
    "limitations": MetaIntent.LIMITS,
    "limits": MetaIntent.LIMITS,
    "what cant you do": MetaIntent.LIMITS,
    "what can't you do": MetaIntent.LIMITS,
    "hi": MetaIntent.GREETING,
    "hello": MetaIntent.GREETING,
    "hey": MetaIntent.GREETING,
    "thanks": MetaIntent.THANKS,
    "thank you": MetaIntent.THANKS,
}

DOMAIN_KEYWORDS = {
    "transfer",
    "send",
    "pay",
    "airtime",
    "data",
    "balance",
    "transaction",
    "transactions",
    "statement",
    "history",
    "receipt",
    "receipts",
    "support",
    "ticket",
    "issue",
    "failed",
    "debit",
    "credit",
    "account",
    "accounts",
    "beneficiary",
    "save",
    "add",
    "delete",
    "buy",
    "recharge",
    "topup",
    "pin",
    "otp",
    "bank",
    "card",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text)


def detect_meta_intent(message_text: str) -> MetaIntent | None:
    """Deterministically detect meta intent from message."""
    if not message_text:
        return None

    text = message_text.strip().lower()
    if not text:
        return None

    # 1. Check for domain keywords (Block meta if domain is present)
    tokens = set(_tokenize(text))
    if tokens & DOMAIN_KEYWORDS:
        return None

    # 2. Exact Match / Phrase Match
    for phrase, intent in META_INTENT_MAP.items():
        if phrase in text:
            return intent

    return None


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Fast Path Gate with Cached Meta Support.

    1. Check for Active Sessions (Input Interrupt).
    2. Check for Meta Intent (Cached).
    3. Check for Query Continuation (Regex).
    4. Fallback to Planner.
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
                        return {
                            "pending_interrupt": None,
                            "tasks": {},
                            "waves": [],
                            "fast_path_triggered": True,
                            "final_response": "Cancelled.",
                        }

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

    # --- 2. Cached Meta Reply (Zero/One Shot) ---
    allow_meta = state.pending_interrupt is None and not state.tasks and not state.waves
    meta_intent = detect_meta_intent(message_text) if allow_meta else None

    if meta_intent:
        logger.info("gate_meta_intent_detected", intent=meta_intent.value)

        task_planner = config["configurable"].get("task_planner")
        redis_client = config["configurable"].get("redis_client")
        llm = task_planner.planner_llm if task_planner and hasattr(task_planner, "planner_llm") else None

        active_session = {"domain": session.domain, "state": session.state} if session else None

        message, handoff = await generate_meta_reply(
            llm,
            user_message=message_text,
            user_language_hint=state.loaded_context.get("language"),
            meta_intent=meta_intent,
            redis_client=redis_client,
            active_session=active_session,
        )

        if handoff == "meta":
            return {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "planner_output": None,
                "fast_path_triggered": True,
                "outbox": [{"type": "say", "text": message}],
                "final_response": message,
            }

        logger.info("gate_meta_handoff_domain", input=message_text[:40])

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
