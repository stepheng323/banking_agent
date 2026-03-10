"""Planner guardrail helper functions."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.nodes.planner_fastpath import TRANSACTION_EXECUTORS
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiaryRouteDecision(BaseModel):
    """LLM disambiguation between beneficiary management and recipient analytics."""

    route: Literal["beneficiary_list", "recipient_ranking", "other"] = Field(
        default="other",
        description=(
            "Route beneficiary-like request either to beneficiary list management, "
            "recipient ranking analytics, or other."
        ),
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


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


async def _repair_beneficiary_summary_misroute(
    planner_output: Any,
    *,
    user_text: str,
    task_planner: Any,
) -> Any:
    """Rewrite mistaken query beneficiary-summary tasks using LLM-directed disambiguation."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output

    text = (user_text or "").strip()
    if not text:
        return planner_output

    summary_tasks = [
        task
        for task in planner_output.tasks
        if getattr(task, "executor", None) == "query" and getattr(task, "action", None) == "beneficiary_summary"
    ]
    if not summary_tasks:
        return planner_output

    planner_llm = getattr(task_planner, "planner_llm", None)
    if planner_llm is None or not callable(getattr(planner_llm, "with_structured_output", None)):
        return planner_output

    try:
        structured = planner_llm.with_structured_output(BeneficiaryRouteDecision)
        decision = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Classify the user's intent for routing. "
                        "Use 'beneficiary_list' when they ask to view/manage saved beneficiaries. "
                        "Use 'recipient_ranking' when they ask who they send money to most/top recipients. "
                        "Use 'other' otherwise."
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        parsed = decision if isinstance(decision, BeneficiaryRouteDecision) else BeneficiaryRouteDecision(**decision)
    except Exception as exc:
        logger.warning("beneficiary_route_disambiguation_failed", error=str(exc))
        return planner_output

    if parsed.route != "beneficiary_list":
        return planner_output

    for task in summary_tasks:
        task.executor = "beneficiary"
        task.action = "list_beneficiaries"
        task.risk = "READ_ONLY"

    if len(planner_output.tasks) == len(summary_tasks):
        planner_output.primary_intent = "beneficiary"
        planner_output.is_complex = False

    planner_output.response = ""
    planner_output.response_key = None
    logger.info(
        "beneficiary_summary_misroute_repaired",
        rewired_tasks=len(summary_tasks),
        confidence=parsed.confidence,
    )
    return planner_output


__all__ = [
    "_deescalate_mandate_acknowledgement",
    "_filter_spurious_affirmation_tasks",
    "_repair_beneficiary_summary_misroute",
]
