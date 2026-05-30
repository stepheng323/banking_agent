from typing import Any, cast

from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.context_router import (
    _build_active_task_router_state,
    _build_interrupt_context,
    _build_interrupt_context_details,
    _clip_text,
    _compact_task_payload_for_interrupt_router,
    _minimal_task_payload_for_interrupt_router,
    _select_interrupt_router_prompt_mode,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS,
    INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS,
    INTERRUPT_PROMPT_COMPACT_MAX_CHARS,
    INTERRUPT_PROMPT_MAX_CHARS,
    INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS,
    INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
    TRANSACTION_INTENTS,
)
from banking.presentation.i18n.locale import LocaleManager
from shared.utils.logging import get_logger

logger = get_logger("apps.chat.src.agent.orchestrator.workflows.interrupt")


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize((state.loaded_context or {}).get("language")).value)


def _current_task_types(state: OrchestratorState, task_ids: list[str]) -> set[str]:
    return {state.tasks[tid].type for tid in task_ids if tid in state.tasks}


def _active_intent(current_task_types: set[str]) -> str:
    return next(iter(current_task_types)) if current_task_types else "unknown"


def _next_interrupt_task_id(
    *,
    state: OrchestratorState,
    target_intent: str,
    start_index: int = 1,
) -> str:
    index = max(start_index, 1)
    while True:
        candidate = f"interrupt_{target_intent}_{index}"
        if candidate not in state.tasks:
            return candidate
        index += 1


def _clear_current_domain_sessions(state: OrchestratorState, domains: set[str]) -> tuple[list[Any], str | None]:
    if not domains:
        stack = list(state.session_stack)
        return stack, (stack[-1].domain if stack else None)

    stack = [session for session in state.session_stack if session.domain not in domains]
    return stack, (stack[-1].domain if stack else None)


def _should_stash_switch(current_task_types: set[str], new_task_types: set[str]) -> bool:
    return (
        bool(current_task_types)
        and current_task_types.issubset(TRANSACTION_INTENTS)
        and not new_task_types.issubset(TRANSACTION_INTENTS)
    )


def _is_transaction_intent(intent: str | None) -> bool:
    return bool(intent and intent in TRANSACTION_INTENTS)


def _is_transaction_replacement(
    *,
    current_task_types: set[str],
    new_task_types: set[str],
    primary_intent: str | None,
) -> bool:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        return False
    if _is_transaction_intent(primary_intent):
        return True
    return bool(new_task_types) and new_task_types.issubset(TRANSACTION_INTENTS)


def _is_resumable_interrupt(interrupt: Any) -> bool:
    kind = getattr(interrupt, "kind", None)
    task_ids = getattr(interrupt, "task_ids", None)
    return kind in {"input", "confirmation", "auth"} and isinstance(task_ids, list) and bool(task_ids)


async def _cancel_updates(
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
) -> dict[str, Any]:
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    return {
        **reset_updates,
        "last_interrupt": interrupt,
        "final_response": cancelled_message(state),
    }


__all__ = [
    "INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS",
    "INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS",
    "INTERRUPT_PROMPT_COMPACT_MAX_CHARS",
    "INTERRUPT_PROMPT_MAX_CHARS",
    "INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS",
    "INTERRUPT_REQUIRED_FIELDS_MAX_CHARS",
    "TRANSACTION_INTENTS",
    "logger",
    "_active_intent",
    "_build_active_task_router_state",
    "_build_interrupt_context",
    "_build_interrupt_context_details",
    "_cancel_updates",
    "_clear_current_domain_sessions",
    "_clip_text",
    "_compact_task_payload_for_interrupt_router",
    "_current_task_types",
    "_is_resumable_interrupt",
    "_is_transaction_intent",
    "_is_transaction_replacement",
    "_minimal_task_payload_for_interrupt_router",
    "_next_interrupt_task_id",
    "_select_interrupt_router_prompt_mode",
    "_should_stash_switch",
    "_state_locale",
]
