"""Signal-driven selector for planner prompt bundles and rules."""

from __future__ import annotations

from shared.services.task_planner_prompt_atoms import (
    PLANNER_BASE_RULE_ATOMS,
    PLANNER_CONTEXT_RULE_ATOMS,
    PLANNER_MONEY_MOVE_RULE_ATOMS,
    PLANNER_QUERY_RULE_ATOMS,
    PLANNER_RULE_ATOM_ORDER,
)
from shared.services.task_planner_prompt_models import PlannerPromptSignals

_TRANSACTIONAL_EXECUTORS = {"transfer", "airtime", "data"}


def _has_transactional_active_flow(signals: PlannerPromptSignals) -> bool:
    return bool(signals.active_flow_type and signals.active_flow_type in _TRANSACTIONAL_EXECUTORS)


def _include_money_move_bundle(signals: PlannerPromptSignals) -> bool:
    if signals.expected_transaction_executors:
        return True
    if _has_transactional_active_flow(signals):
        return True
    if signals.pending_interrupt_kind in {"confirmation", "auth"}:
        return True
    return False


def _include_query_bundle(signals: PlannerPromptSignals) -> bool:
    if signals.query_session_active:
        return True
    return signals.recent_domain_focus == "query"


def _include_context_bundle(signals: PlannerPromptSignals) -> bool:
    if signals.has_beneficiary_suggestion:
        return True
    if signals.pending_interrupt_kind is not None:
        return True
    if signals.active_flow_type is not None:
        return True
    return False


def _include_executor_coverage_guard(signals: PlannerPromptSignals) -> bool:
    return bool(signals.expected_transaction_executors)


def select_prompt_bundles(signals: PlannerPromptSignals) -> tuple[str, ...]:
    selected: list[str] = []
    if _include_money_move_bundle(signals):
        selected.append("money_move")
    if _include_query_bundle(signals):
        selected.append("query")
    if _include_context_bundle(signals):
        selected.append("context")
    if _include_executor_coverage_guard(signals):
        selected.append("executor_coverage_guard")
    return tuple(selected)


def select_rule_ids(signals: PlannerPromptSignals) -> tuple[str, ...]:
    selected = set(PLANNER_BASE_RULE_ATOMS)
    bundles = set(select_prompt_bundles(signals))

    if "money_move" in bundles:
        selected.update(PLANNER_MONEY_MOVE_RULE_ATOMS)
    if "query" in bundles:
        selected.update(PLANNER_QUERY_RULE_ATOMS)
    if "context" in bundles:
        selected.update(PLANNER_CONTEXT_RULE_ATOMS)

    return tuple(rule_id for rule_id in PLANNER_RULE_ATOM_ORDER if rule_id in selected)


__all__ = ["select_prompt_bundles", "select_rule_ids"]
