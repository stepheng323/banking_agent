"""Pending-mandate guardrails for planner output."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import TRANSACTION_EXECUTORS
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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


__all__ = ["_deescalate_mandate_acknowledgement", "_has_pending_mandate_without_ready_accounts"]
