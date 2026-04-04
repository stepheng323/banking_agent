"""Signal-driven selector for planner prompt bundles and rules."""

from __future__ import annotations

from shared.services.task_planner_prompt_atoms import (
    PLANNER_BASE_RULE_ATOMS,
    PLANNER_CONTEXT_RULE_ATOMS,
    PLANNER_MIXED_TX_RULE_ATOMS,
    PLANNER_MONEY_MOVE_RULE_ATOMS,
    PLANNER_RULE_ATOM_ORDER,
    PLANNER_TRANSFER_ONLY_RULE_ATOMS,
)
from shared.services.task_planner_prompt_models import PlannerPromptSignals

_TRANSACTIONAL_EXECUTORS = {"transfer", "airtime", "data"}


def _has_transactional_active_flow(signals: PlannerPromptSignals) -> bool:
    return bool(signals.active_flow_type and signals.active_flow_type in _TRANSACTIONAL_EXECUTORS)


def _is_transactional_interrupt(signals: PlannerPromptSignals) -> bool:
    if signals.pending_interrupt_kind is None:
        return False
    if _has_transactional_active_flow(signals):
        return True
    if signals.forced_domain_owner == "transfer":
        return True
    return bool(signals.expected_transaction_executors)


def _include_money_move_bundle(signals: PlannerPromptSignals) -> bool:
    if _include_transfer_only_bundle(signals):
        return False
    if _include_mixed_tx_bundle(signals):
        return False
    if signals.expected_transaction_executors:
        return True
    if signals.has_transaction_intent_hint:
        return True
    if _has_transactional_active_flow(signals):
        return True
    if signals.pending_interrupt_kind in {"confirmation", "auth"}:
        return True
    return False

def _include_context_bundle(signals: PlannerPromptSignals) -> bool:
    if signals.has_beneficiary_suggestion:
        return True
    if signals.active_flow_type is not None and signals.active_flow_type not in _TRANSACTIONAL_EXECUTORS:
        return True
    if signals.pending_interrupt_kind is not None:
        return not _is_transactional_interrupt(signals)
    return False


def _include_transfer_only_bundle(signals: PlannerPromptSignals) -> bool:
    if signals.forced_domain_owner != "transfer":
        return False
    if signals.expected_transaction_executors != ("transfer",):
        return False
    if signals.has_quote:
        return False
    if signals.query_session_active:
        return False
    if signals.active_flow_type is not None and signals.active_flow_type != "transfer":
        return False
    return True


def _include_mixed_tx_bundle(signals: PlannerPromptSignals) -> bool:
    expected = signals.expected_transaction_executors
    if len(expected) < 2:
        return False
    if signals.has_quote:
        return False
    if signals.pending_interrupt_kind is not None:
        return False
    if _has_transactional_active_flow(signals):
        return False
    if signals.query_session_active:
        return False
    return True


def _include_executor_coverage_guard(signals: PlannerPromptSignals) -> bool:
    if _include_transfer_only_bundle(signals):
        return False
    return len(signals.expected_transaction_executors) > 1


def select_prompt_bundles(signals: PlannerPromptSignals) -> tuple[str, ...]:
    selected: list[str] = []
    if _include_transfer_only_bundle(signals):
        selected.append("transfer_only")
    if _include_mixed_tx_bundle(signals):
        selected.append("mixed_tx")
    if _include_money_move_bundle(signals):
        selected.append("money_move")
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
    if "transfer_only" in bundles:
        selected.update(PLANNER_TRANSFER_ONLY_RULE_ATOMS)
    if "mixed_tx" in bundles:
        selected.update(PLANNER_MIXED_TX_RULE_ATOMS)
    if "context" in bundles:
        selected.update(PLANNER_CONTEXT_RULE_ATOMS)

    return tuple(rule_id for rule_id in PLANNER_RULE_ATOM_ORDER if rule_id in selected)


__all__ = ["select_prompt_bundles", "select_rule_ids"]
