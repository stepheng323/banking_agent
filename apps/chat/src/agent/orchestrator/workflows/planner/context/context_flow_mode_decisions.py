"""Planner context mode selection heuristics."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import TRANSACTION_EXECUTORS
from shared.types.planner import RouterDomainIntent, TransactionExecutor


def _should_use_minimal_planner_context(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    query_session_active: bool,
    recent_domain_focus: str | None,
    recent_answer_focus: str | None,
    has_transaction_intent_hint: bool,
) -> bool:
    """Skip full turn-context assembly when no live state needs preservation."""
    if state.pending_interrupt is not None:
        return False
    if state.has_quote:
        return False
    if state.session_stack:
        return False
    if active_intent is not None:
        return False
    if query_session_active:
        return False
    if recent_domain_focus is not None:
        return False
    if recent_answer_focus is not None:
        return False
    if has_transaction_intent_hint:
        return False
    return True


def _is_narrow_transfer_replan(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state.has_quote:
        return False
    if query_session_active:
        return False
    if state.pending_interrupt is None:
        return False
    if active_intent != "transfer":
        task_ids = getattr(state.pending_interrupt, "task_ids", None) or []
        active_interrupt_types = {
            state.tasks[task_id].type
            for task_id in task_ids
            if isinstance(task_id, str) and task_id in state.tasks
        }
        if active_interrupt_types != {"transfer"} and state.routing_target_domain != "transfer":
            return False
    return expected_executors in {(), ("transfer",)}


def _forced_domain_owner(
    state: OrchestratorState,
    *,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> RouterDomainIntent | None:
    if _is_narrow_transfer_replan(
        state=state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    ):
        return "transfer"
    if state.pending_interrupt is not None:
        return None
    if state.has_quote:
        return None
    if state.direct_path_triggered:
        return None
    if state.routing_owner != "guardrail":
        return None
    if state.routing_target_domain != "transfer":
        return None
    if tuple(state.preplanner_expected_transaction_executors) != ("transfer",):
        return None
    if state.routing_decision not in {"batch_transfer_command", "account_aware_transfer_command"}:
        return None
    return "transfer"


def _should_use_compact_transaction_context(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    forced_domain_owner: RouterDomainIntent | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state.has_quote:
        return False
    if query_session_active:
        return False
    if not expected_executors:
        return False
    if any(executor not in TRANSACTION_EXECUTORS for executor in expected_executors):
        return False
    if forced_domain_owner == "transfer":
        return True
    if len(expected_executors) >= 2:
        return True
    if state.pending_interrupt is None:
        return False
    if active_intent in TRANSACTION_EXECUTORS:
        return True
    return state.routing_target_domain in TRANSACTION_EXECUTORS


__all__ = [
    "_forced_domain_owner",
    "_is_narrow_transfer_replan",
    "_should_use_compact_transaction_context",
    "_should_use_minimal_planner_context",
]
