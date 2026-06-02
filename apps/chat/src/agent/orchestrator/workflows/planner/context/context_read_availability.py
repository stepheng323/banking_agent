"""Loaded-context availability and list limits for planner context reads."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    BENEFICIARY_MATCH_PREVIEW_LIMIT,
    CONTEXT_READ_BENEFICIARY_SUBTYPES,
    CONTEXT_READ_FLOW_SUBTYPES,
    CONTEXT_READ_LIST_LIMIT,
    TRANSACTION_EXECUTORS,
)
from banking.accounts.mandate_state import READY, effective_mandate_status


def _has_context_for_read_subtype(state: OrchestratorState, subtype: str) -> bool:
    """Check whether current loaded context is sufficient for a context-read answer."""
    ctx: dict[str, Any] = state.loaded_context or {}
    accounts_raw = ctx.get("accounts")
    beneficiaries_raw = ctx.get("beneficiaries")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []

    if subtype in {"account_count", "linked_accounts_summary", "account_mandate_readiness_summary"}:
        return isinstance(accounts_raw, list)
    if subtype == "account_linked_bank_existence_check":
        return isinstance(accounts_raw, list) and bool(accounts)
    if subtype == "default_account_identity":
        return isinstance(accounts_raw, list) and any(bool(acc.get("is_default")) for acc in accounts)
    if subtype == "pending_mandate_explanation":
        return isinstance(accounts_raw, list) and any(
            bool(status := effective_mandate_status(acc)) and status != READY for acc in accounts
        )
    if subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
        return isinstance(beneficiaries_raw, list)
    if subtype in CONTEXT_READ_FLOW_SUBTYPES:
        pending_interrupt = state.pending_interrupt
        if not pending_interrupt or not pending_interrupt.task_ids:
            return False
        task_types = {
            state.tasks[tid].type for tid in pending_interrupt.task_ids if tid in state.tasks and state.tasks[tid]
        }
        return bool(task_types) and task_types.issubset(TRANSACTION_EXECUTORS)
    return False


def _context_read_total_items(state: OrchestratorState, subtype: str) -> int | None:
    """Return total list size for list-style context-read requests."""
    ctx = state.loaded_context or {}
    if subtype == "linked_accounts_summary":
        accounts = ctx.get("accounts")
        return len(accounts) if isinstance(accounts, list) else None
    if subtype == "beneficiary_list":
        beneficiaries = ctx.get("beneficiaries")
        return len(beneficiaries) if isinstance(beneficiaries, list) else None
    if subtype == "beneficiary_name_match_preview":
        beneficiaries = ctx.get("beneficiaries")
        return len(beneficiaries) if isinstance(beneficiaries, list) else None
    return None


def _context_read_shown_limit(subtype: str) -> int:
    if subtype == "beneficiary_name_match_preview":
        return BENEFICIARY_MATCH_PREVIEW_LIMIT
    return CONTEXT_READ_LIST_LIMIT


__all__ = ["_context_read_shown_limit", "_context_read_total_items", "_has_context_for_read_subtype"]
