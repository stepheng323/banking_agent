"""Beneficiary routing contract guardrails for planner output."""

import hashlib
from typing import Any

from shared.utils.logging import get_logger

logger = get_logger(__name__)
_BENEFICIARY_ROUTE_HINTS = {"beneficiary_list", "recipient_ranking", "none"}
_BENEFICIARY_MANAGEMENT_ACTIONS = {
    "list_beneficiaries",
    "add_beneficiary",
    "delete_beneficiary",
    "update_beneficiary",
}


def _routing_contract_context(
    planner_output: Any,
    *,
    message_id: str | None,
    user_text: str,
    has_beneficiary_suggestion: bool,
) -> dict[str, Any]:
    tasks = list(getattr(planner_output, "tasks", None) or [])
    task_shapes = [f"{getattr(task, 'executor', None)}.{getattr(task, 'action', None)}" for task in tasks]
    message_hash = hashlib.sha256((user_text or "").encode("utf-8")).hexdigest()[:12]
    return {
        "message_id": message_id,
        "message_hash": message_hash,
        "beneficiary_route": str(getattr(planner_output, "beneficiary_route", "none") or "none").strip().lower(),
        "has_beneficiary_suggestion": has_beneficiary_suggestion,
        "primary_intent": getattr(planner_output, "primary_intent", None),
        "task_count": len(tasks),
        "task_shapes": task_shapes,
    }


def _beneficiary_contract_violations(planner_output: Any) -> list[str]:
    tasks = list(getattr(planner_output, "tasks", None) or [])
    route_hint = str(getattr(planner_output, "beneficiary_route", "none") or "none").strip().lower()
    if route_hint not in _BENEFICIARY_ROUTE_HINTS:
        route_hint = "none"

    ranking_tasks = [
        task
        for task in tasks
        if getattr(task, "executor", None) == "query" and getattr(task, "action", None) == "beneficiary_summary"
    ]
    beneficiary_tasks = [task for task in tasks if getattr(task, "executor", None) == "beneficiary"]
    save_tasks = [task for task in beneficiary_tasks if getattr(task, "action", None) == "save_beneficiary"]
    management_tasks = [
        task for task in beneficiary_tasks if getattr(task, "action", None) in _BENEFICIARY_MANAGEMENT_ACTIONS
    ]

    violations: list[str] = []

    if route_hint == "beneficiary_list" and ranking_tasks:
        violations.append("beneficiary_route_contract_violation")

    if route_hint == "recipient_ranking" and (beneficiary_tasks or management_tasks):
        violations.append("beneficiary_route_contract_violation")

    if route_hint == "none" and (ranking_tasks or management_tasks):
        violations.append("beneficiary_route_contract_violation")

    if save_tasks and route_hint != "none":
        violations.append("beneficiary_route_contract_violation")

    if save_tasks:
        violations.append("save_beneficiary_gate_only")

    return violations


def _apply_clarify_fallback(planner_output: Any) -> Any:
    planner_output.tasks = []
    planner_output.primary_intent = "conversational"
    planner_output.is_complex = False
    planner_output.response = ""
    planner_output.response_key = "conversational.clarify"
    planner_output.context_read_subtype = None
    return planner_output


def _enforce_beneficiary_routing_contract(
    planner_output: Any,
    *,
    message_id: str | None,
    user_text: str,
    has_beneficiary_suggestion: bool,
) -> Any:
    """Validate first-pass beneficiary routing; clarify on contract violations."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output

    violations = _beneficiary_contract_violations(planner_output)
    if not violations:
        return planner_output

    context = _routing_contract_context(
        planner_output,
        message_id=message_id,
        user_text=user_text,
        has_beneficiary_suggestion=has_beneficiary_suggestion,
    )
    for code in sorted(set(violations)):
        logger.warning(code, **context)

    logger.info(
        "planner_routing_contract_clarify_fallback",
        violation_count=len(violations),
        violation_categories=sorted(set(violations)),
        **context,
    )
    planner_output = _apply_clarify_fallback(planner_output)
    return planner_output


__all__ = ["_enforce_beneficiary_routing_contract"]
