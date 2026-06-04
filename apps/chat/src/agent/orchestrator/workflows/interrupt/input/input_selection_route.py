"""Deterministic selection shortcuts for input interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.transactions.shared.account_selection.reference import match_source_account_reference
from shared.types.planner import InterruptRouteDecision


def _resolve_deterministic_input_selection_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "input":
        return None

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = fields_by_task.get(task_ids[0]) or []
    if set(required_fields) != {"source_account_id"}:
        return None
    if not text.strip().isdigit():
        loaded_context = interrupt_state_view(state).loaded_context_or_empty
        accounts = (
            loaded_context.get("transaction_accounts")
            or loaded_context.get("accounts")
            or loaded_context.get("all_accounts")
        )
        if not isinstance(accounts, list) or not match_source_account_reference(
            text,
            [account for account in accounts if isinstance(account, dict)],
        ):
            return None

    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=0.99,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason="shortcut_input_numeric_selection",
    )


__all__ = ["_resolve_deterministic_input_selection_route"]
