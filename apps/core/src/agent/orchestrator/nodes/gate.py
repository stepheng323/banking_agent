"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements the 3-Tier Routing Logic.
"""

import re
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.system_profile import SYSTEM_PROFILE
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class GateClassification(BaseModel):
    """Output for Tier 1 Gate Classifier."""

    decision: Literal["CONTINUE", "NEW", "ASK"] = Field(description="Action to take")
    domain: str | None = Field(description="Target domain if NEW or CONTINUE")
    confidence: float = Field(description="Confidence score 0.0-1.0")


META_STRONG_PHRASES = (
    "who are you",
    "what can you do",
    "what do you do",
    "your name",
    "what is your name",
    "are you an ai",
    "are you a bot",
    "are you a robot",
    "are you human",
    "what are your capabilities",
    "capabilities",
    "features",
    "limitations",
    "limits",
    "what can't you do",
    "what cant you do",
    "lotr",
    "lord of the rings",
    "samwise",
)

META_WEAK_WORDS = {
    "hi",
    "hello",
    "hey",
    "hola",
    "help",
    "menu",
    "start",
    "assist",
    "bawo",
    "sannu",
    "kedu",
}

META_WEAK_PHRASES = (
    "good morning",
    "good afternoon",
    "good evening",
    "how far",
    "how can you help",
    "wetin you fit do",
)

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


def _matches_meta_intent(message_text: str, profile_name: str) -> bool:
    if not message_text:
        return False
    text = message_text.strip().lower()
    if not text:
        return False

    for phrase in META_STRONG_PHRASES:
        if phrase in text:
            return True

    tokens = set(_tokenize(text))
    if tokens & DOMAIN_KEYWORDS:
        return False

    if profile_name and profile_name in text:
        return True

    if tokens & META_WEAK_WORDS:
        return True

    return any(phrase in text for phrase in META_WEAK_PHRASES)


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Tier 0 (Deterministic) & Tier 1 (Lightweight) Gate.

    1. Check for Active Sessions on stack.
    2. If found, apply deterministic rules (Fast Match).
    3. If Fast Match succeeds -> Skip Planner, Route to execution.
    4. If Fast Match fails -> Tier 1 (Cheap Classifer).
    5. If Tier 1 fails/ambiguous -> Fallback to Planner (Tier 2).
    """

    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    if state.pending_interrupt and state.pending_interrupt.kind == "input":
        if session and session.state == "WAITING_FOR_INPUT":
            domain_allowlist = {"transfer", "airtime", "data", "support"}
            if session.domain in domain_allowlist:
                if state.last_message_text:
                    updates = {"pending_interrupt": None, "tasks": {}, "fast_path_triggered": True}
                    for tid in state.pending_interrupt.task_ids:
                        task = state.tasks[tid].model_copy(deep=True)
                        task.stage = TaskStage.EXTRACTED
                        task.payload["confirmation"] = {}
                        updates["tasks"][tid] = task

                    logger.info("fast_path_input_match", domain=session.domain, tasks=state.pending_interrupt.task_ids)
                    return updates

    message_text = (state.last_message_text or "").strip()
    profile_name = SYSTEM_PROFILE.name.lower()

    allow_meta = state.pending_interrupt is None and not state.tasks and not state.waves
    if allow_meta and _matches_meta_intent(message_text, profile_name):
        logger.info("gate_meta_intent_detected", input=message_text[:40])

        task_planner = config["configurable"].get("task_planner")
        llm = task_planner.planner_llm if task_planner and hasattr(task_planner, "planner_llm") else None
        active_session = {"domain": session.domain, "state": session.state} if session else None
        message, handoff = await generate_meta_reply(
            llm,
            user_message=message_text,
            user_language_hint=state.loaded_context.get("language"),
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
        message_text = message_text.lower()
        logger.info("gate_tier0_check", domain=session.domain, input_fragment=message_text[:20])

        # Rule A: Query Continuation
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

            first_word = message_text.split()[0] if message_text else ""

            is_fast_match = (
                first_word in fast_keywords or "page" in message_text or "only" in message_text  # "only credits"
            )

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

    # --- Tier 1: Cheap Classifier (Lightweight LLM) ---
    if session and not state.pending_interrupt:
        logger.info("gate_tier1_check", domain=session.domain)
        task_planner = config["configurable"].get("task_planner")
        if task_planner and hasattr(task_planner, "planner_llm"):
            try:
                # Minimal prompt to save tokens and latency
                prompt = f"""Active Domain: {session.domain}
User Message: "{state.last_message_text}"
Is this confirming/continuing the active domain, or a new request?
Respond JSON: decision (CONTINUE, NEW, ASK), domain, confidence."""

                classifier = task_planner.planner_llm.with_structured_output(GateClassification)
                result = await classifier.ainvoke(prompt)

                logger.info(
                    "gate_classifier_result", decision=result.decision, domain=result.domain, conf=result.confidence
                )

                if result.decision == "CONTINUE" and result.confidence > 0.8:
                    allowlist = {"query", "transfer", "airtime", "data", "support"}
                    if session.domain in allowlist:
                        task_id = f"fast_{session.domain}_resume"
                        spec = TaskSpec(
                            id=task_id,
                            type=session.domain,
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

            except Exception as e:
                logger.warning("gate_classifier_failed", error=str(e))

    logger.info("gate_fallback_to_planner", reason="no_fast_path_match")
    return {}  # Fallback to planner logic
