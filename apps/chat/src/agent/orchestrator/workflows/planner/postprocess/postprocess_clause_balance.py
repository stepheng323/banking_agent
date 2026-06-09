"""Balance-task synthesis for clause-based planner repairs."""

from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_utils import (
    _coerce_clause_field_text,
    _next_clause_repair_task_id,
    _normalize_clause_family,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_common import (
    _normalize_recipient_text,
)
from shared.types.planner import AccountTaskParameters, PlannedTask, PlannerClause, make_planned_task


def _balance_like_clause(clause: PlannerClause) -> bool:
    family = _normalize_clause_family(clause.intent_family)
    if family != "account_query":
        return False
    action_hint = _coerce_clause_field_text(clause, "account_action_hint", "action", "query_kind")
    if action_hint and action_hint.strip().lower().replace(" ", "_") in {
        "check_balance",
        "balance",
        "show_balance",
        "overall_balance",
    }:
        return True
    normalized_text = _normalize_recipient_text(clause.text)
    return any(token in normalized_text for token in ("balance", "remaining", "remain", "left"))


def _build_balance_task_from_clause(
    clause: PlannerClause,
    *,
    existing_ids: set[str],
    depends_on: list[str],
) -> PlannedTask:
    return make_planned_task(
        task_id=_next_clause_repair_task_id("account", existing_ids, clause.clause_index),
        action="check_balance",
        executor="account",
        instruction=clause.text or "Show my balance",
        parameters=AccountTaskParameters(),
        depends_on=depends_on,
        risk="READ_ONLY",
        source_clause_index=clause.clause_index,
    )


__all__ = ["_balance_like_clause", "_build_balance_task_from_clause"]
