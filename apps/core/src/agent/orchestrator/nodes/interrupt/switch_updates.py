import re
import time
from typing import Any, cast

from apps.core.src.agent.orchestrator.models.domain import TaskSpec
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.interrupt.context import (
    _clear_current_domain_sessions,
    _is_resumable_interrupt,
    _is_transaction_replacement,
    _should_stash_switch,
    logger,
)
from shared.types.planner import (
    PlannerOutput,
)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320
_CONFIRMATION_UPDATE_VERB_RE = re.compile(
    r"\b(change|update|edit|instead|set|make(?:\s+it)?|replace|correct|meant|add|use)\b",
    re.IGNORECASE,
)
_CONFIRMATION_UPDATE_FIELD_RE = re.compile(
    r"\b(amount|bank|account|recipient|beneficiary|narration|memo|note|description)\b",
    re.IGNORECASE,
)
_CONFIRMATION_NOTE_FIELD_RE = re.compile(r"\b(narration|memo|note|description|reason|purpose)\b", re.IGNORECASE)
_CONFIRMATION_ITS_FOR_RE = re.compile(r"\b(?:it'?s|its|it is|this is)\s+for\b", re.IGNORECASE)
_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:^|\s)(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?(?:\s|$)",
    re.IGNORECASE,
)
_INPUT_SIMPLE_AMOUNT_REPLY_RE = re.compile(r"^(?:₦?\d[\d,]*(?:\.\d+)?k?|all|everything|half|50%)$", re.IGNORECASE)
_CONFIRMATION_ACCOUNT_BANK_REPLY_RE = re.compile(r"[a-zA-Z].*\d[\d\s,.\-]{8,}|\d[\d\s,.\-]{8,}.*[a-zA-Z]")
_NON_TRANSFER_INTENT_HINT_RE = re.compile(
    r"\b(airtime|data|bundle|balance|statement|support|faq|ticket|complaint)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|this is)\s+)?(?:(?:to|for|send(?:\s+it)?\s+to)\s+)?(?P<recipient>.+?)$",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_BLOCK_RE = re.compile(
    r"\b(and|also|plus|then|while|cancel|stop|show|list|check|buy|help|support|faq|balance|statement|spend|spent|transaction|transactions|airtime|data|beneficiar(?:y|ies)|account(?:s)?|week|month|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_QUESTION_RE = re.compile(r"^(what|how|why|when|where|who|which)\b", re.IGNORECASE)
_INPUT_RECIPIENT_REPLY_META_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)$",
    re.IGNORECASE,
)
_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)
_CONFIRMATION_MULTI_CLAUSE_SPLIT_RE = re.compile(r"\s+(?:and|then)\s+|[;\n]+|,\s*", re.IGNORECASE)
_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_SCOPED_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)

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
        "stashed_at_ts": int(time.time()),
    }
    return cast(list[dict[str, Any]], state.stashed_sessions + [current_session])

def _build_stash_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)

    logger.info(
        "interrupt_replan_switched",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        stashed=True,
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "stashed_sessions": stashed,
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_replace_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    logger.info(
        "interrupt_replan_replaced",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_transaction_replacement_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack = [session for session in state.session_stack if session.domain not in TRANSACTION_INTENTS]
    logger.info(
        "interrupt_transaction_replaced",
        kind=interrupt.kind,
        cancelled_task_ids=interrupt.task_ids,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_planner_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    text: str,
    expected_executors: list[str],
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": None,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if expected_executors:
        updates["preplanner_expected_transaction_executors"] = expected_executors

    if current_task_types.issubset(TRANSACTION_INTENTS) and _is_resumable_interrupt(interrupt):
        stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
        updates["stashed_sessions"] = stashed
        logger.info(
            "interrupt_switch_to_planner_stashed",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    else:
        logger.info(
            "interrupt_switch_to_planner_replaced",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    return updates

def _switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
    primary_intent: str | None,
) -> dict[str, Any]:
    if _is_transaction_replacement(
        current_task_types=current_task_types,
        new_task_types=new_task_types,
        primary_intent=primary_intent,
    ):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    if _should_stash_switch(current_task_types, new_task_types):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    return _build_replace_switch_updates(
        state=state,
        interrupt=interrupt,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=planner_output,
    )
