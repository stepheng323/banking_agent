"""Planner guardrail helper functions."""

import hashlib
from typing import Any

from apps.core.src.agent.orchestrator.nodes.planner_context_read import TRANSACTION_EXECUTORS
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_BENEFICIARY_ROUTE_HINTS = {"beneficiary_list", "recipient_ranking", "none"}
_BENEFICIARY_MANAGEMENT_ACTIONS = {
    "list_beneficiaries",
    "add_beneficiary",
    "delete_beneficiary",
    "update_beneficiary",
}


def _filter_spurious_affirmation_tasks(
    planner_output: Any,
    *,
    active_intent: str | None,
    pending_interrupt_kind: str | None,
) -> Any:
    """Drop accidental support tasks when a bare resume-style affirmation is detected."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output
    if not bool(getattr(planner_output, "is_confirmation", False)):
        return planner_output

    tasks = list(planner_output.tasks)
    has_resume = any(
        getattr(task, "executor", None) == "orchestrator" and getattr(task, "action", None) == "resume_session"
        for task in tasks
    )
    has_transaction_task = any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in tasks)
    in_transaction_input_flow = active_intent in TRANSACTION_EXECUTORS and pending_interrupt_kind == "input"
    should_strip_support = has_resume or has_transaction_task or in_transaction_input_flow
    if not should_strip_support:
        return planner_output

    filtered_tasks = [task for task in tasks if getattr(task, "executor", None) != "support"]
    if len(filtered_tasks) == len(tasks):
        return planner_output

    planner_output.tasks = filtered_tasks
    if len(filtered_tasks) == 1:
        planner_output.primary_intent = getattr(filtered_tasks[0], "executor", planner_output.primary_intent)
        planner_output.is_complex = False
    logger.info(
        "spurious_support_task_removed",
        original_count=len(tasks),
        filtered_count=len(filtered_tasks),
        active_intent=active_intent,
        pending_interrupt_kind=pending_interrupt_kind,
    )
    return planner_output


def _has_pending_mandate_without_ready_accounts(loaded_context: dict[str, Any] | None) -> bool:
    if not isinstance(loaded_context, dict):
        return False
    accounts_raw = loaded_context.get("accounts")
    if not isinstance(accounts_raw, list):
        return False

    has_ready = False
    has_pending_like = False
    for account in accounts_raw:
        if not isinstance(account, dict):
            continue
        status = str(account.get("mandate_status") or "").strip().lower()
        if not status:
            continue
        if status == "ready":
            has_ready = True
        else:
            has_pending_like = True

    return has_pending_like and not has_ready


def _deescalate_mandate_acknowledgement(
    planner_output: Any,
    *,
    loaded_context: dict[str, Any] | None,
    locale: str,
) -> Any:
    """Keep pending-mandate turns conversational with contextual destination details."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output
    if not _has_pending_mandate_without_ready_accounts(loaded_context):
        return planner_output

    tasks = list(planner_output.tasks)
    has_transaction_task = any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in tasks)
    if not has_transaction_task:
        return planner_output

    planner_output.tasks = []
    planner_output.primary_intent = "conversational"
    planner_output.is_complex = False
    accounts = loaded_context.get("accounts") if isinstance(loaded_context, dict) else []
    normalized_accounts = (
        [account for account in accounts if isinstance(account, dict)] if isinstance(accounts, list) else []
    )
    planner_output.response = build_pending_mandate_message(normalized_accounts, locale)
    planner_output.response_key = None

    logger.info(
        "pending_mandate_acknowledgement_deescalated",
        original_task_count=len(tasks),
    )
    return planner_output


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


def _beneficiary_contract_violations(planner_output: Any, *, has_beneficiary_suggestion: bool) -> list[str]:
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

    violations = _beneficiary_contract_violations(
        planner_output,
        has_beneficiary_suggestion=has_beneficiary_suggestion,
    )
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


__all__ = [
    "_deescalate_mandate_acknowledgement",
    "_enforce_beneficiary_routing_contract",
    "_filter_spurious_affirmation_tasks",
]
