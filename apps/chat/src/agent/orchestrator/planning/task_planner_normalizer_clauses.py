"""Clause normalization for planner task output."""

from typing import cast

from shared.types.planner import PlannerClause, PlannerClauseIntentFamily, PlannerOutput

_CLAUSE_INTENT_ALIASES = {
    "account": "account_query",
    "account_balance": "account_query",
    "balance": "account_query",
    "balance_query": "account_query",
    "query_balance": "account_query",
    "transaction_query": "query",
    "transactions": "query",
    "meta": "conversational",
}


def _normalize_clause_family(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return "unknown"
    return _CLAUSE_INTENT_ALIASES.get(normalized, normalized)


def normalize_planner_clauses(planner_output: PlannerOutput) -> PlannerOutput:
    raw_clauses = getattr(planner_output, "clauses", None)
    if not raw_clauses:
        return planner_output

    normalized_clauses: list[PlannerClause] = []
    for fallback_index, clause in enumerate(raw_clauses, start=1):
        clause_index = int(getattr(clause, "clause_index", fallback_index) or fallback_index)
        if clause_index < 1:
            clause_index = fallback_index
        extracted_fields = (
            clause.extracted_fields if isinstance(getattr(clause, "extracted_fields", None), dict) else {}
        )
        normalized_clauses.append(
            PlannerClause(
                clause_index=clause_index,
                text=str(getattr(clause, "text", "") or ""),
                intent_family=cast(
                    PlannerClauseIntentFamily,
                    _normalize_clause_family(getattr(clause, "intent_family", None)),
                ),
                extracted_fields=extracted_fields,
                task_ids=[str(task_id) for task_id in getattr(clause, "task_ids", []) if str(task_id).strip()],
            )
        )
    return planner_output.model_copy(update={"clauses": normalized_clauses})
