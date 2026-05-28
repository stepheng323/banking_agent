"""Shared helpers for clause-based planner postprocessing."""

from shared.types.planner import PlannedTask, PlannerClause


def _normalize_clause_family(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"account", "balance", "balance_query", "account_balance"}:
        return "account_query"
    if normalized in {"transaction_query", "transactions"}:
        return "query"
    return normalized or "unknown"


def _clause_to_executor_family(clause: PlannerClause) -> str | None:
    family = _normalize_clause_family(clause.intent_family)
    if family in {"transfer", "airtime", "data", "support", "faq", "beneficiary"}:
        return family
    if family == "account_query":
        return "account_or_query"
    if family == "query":
        return "query"
    return None


def _is_actionable_clause(clause: PlannerClause) -> bool:
    return _clause_to_executor_family(clause) is not None


def _task_matches_clause(task: PlannedTask, clause: PlannerClause) -> bool:
    family = _clause_to_executor_family(clause)
    if family is None:
        return False
    if family == "account_or_query":
        return task.executor in {"account", "query"}
    return task.executor == family


def _next_clause_repair_task_id(prefix: str, existing_ids: set[str], clause_index: int) -> str:
    candidate = f"{prefix}_clause_{clause_index}"
    if candidate not in existing_ids:
        existing_ids.add(candidate)
        return candidate
    suffix = 2
    while True:
        candidate = f"{prefix}_clause_{clause_index}_{suffix}"
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate
        suffix += 1


def _coerce_clause_field_text(clause: PlannerClause, *keys: str) -> str | None:
    for key in keys:
        value = clause.extracted_fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_clause_for_task(task: PlannedTask, clauses: list[PlannerClause]) -> PlannerClause | None:
    if isinstance(task.source_clause_index, int) and task.source_clause_index > 0:
        for clause in clauses:
            if clause.clause_index == task.source_clause_index:
                return clause
    compatible = [clause for clause in clauses if _task_matches_clause(task, clause)]
    if len(compatible) == 1:
        return compatible[0]
    return None


__all__ = [
    "_coerce_clause_field_text",
    "_is_actionable_clause",
    "_next_clause_repair_task_id",
    "_normalize_clause_family",
    "_resolve_clause_for_task",
    "_task_matches_clause",
]
