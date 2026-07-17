"""Planner context mode selection heuristics."""

from apps.chat.src.agent.orchestrator.models.turn_directive import TurnNextStep
from apps.chat.src.agent.orchestrator.workflows.planner.core.domains import TRANSACTION_EXECUTORS
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import RouterDomainIntent, TransactionExecutor


def _should_use_minimal_planner_context(
    *,
    state_view: PlannerStateView,
    active_intent: str | None,
    query_session_active: bool,
    recent_domain_focus: str | None,
    recent_answer_focus: str | None,
    has_transaction_intent_hint: bool,
) -> bool:
    """Skip full turn-context assembly when no live state needs preservation."""
    if state_view.has_pending_interrupt:
        return False
    if state_view.has_quote:
        return False
    if state_view.has_session_stack:
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
    state_view: PlannerStateView,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state_view.has_quote:
        return False
    if query_session_active:
        return False
    if not state_view.has_pending_interrupt:
        return False
    if active_intent != "transfer":
        active_interrupt_types = state_view.pending_interrupt_task_types
        target_domain = state_view.turn_directive.target_domain if state_view.turn_directive else None
        if active_interrupt_types != {"transfer"} and target_domain != "transfer":
            return False
    return expected_executors in {(), ("transfer",)}


def _forced_domain_owner(
    state_view: PlannerStateView,
    *,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> RouterDomainIntent | None:
    if _is_narrow_transfer_replan(
        state_view=state_view,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    ):
        return "transfer"
    if state_view.has_pending_interrupt:
        return None
    if state_view.has_quote:
        return None
    directive = state_view.turn_directive
    if not directive:
        return None
    if directive.next_step != TurnNextStep.PLAN:
        return None
    if directive.owner != "guardrail":
        return None
    if directive.target_domain != "transfer":
        return None
    if not state_view.has_transfer_only_preplanner_expectation:
        return None
    if directive.decision not in {"batch_transfer_command", "account_aware_transfer_command"}:
        return None
    return "transfer"


def _should_use_compact_transaction_context(
    *,
    state_view: PlannerStateView,
    active_intent: str | None,
    forced_domain_owner: RouterDomainIntent | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state_view.has_quote:
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
    if not state_view.has_pending_interrupt:
        return False
    if active_intent in TRANSACTION_EXECUTORS:
        return True
    target_domain = state_view.turn_directive.target_domain if state_view.turn_directive else None
    return target_domain in TRANSACTION_EXECUTORS


__all__ = [
    "_forced_domain_owner",
    "_is_narrow_transfer_replan",
    "_should_use_compact_transaction_context",
    "_should_use_minimal_planner_context",
]
