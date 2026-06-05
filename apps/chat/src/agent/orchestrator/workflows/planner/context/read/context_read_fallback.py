"""Fallback task construction for planner context-read requests."""

from typing import Literal

from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    CONTEXT_READ_ACCOUNT_SUBTYPES,
    CONTEXT_READ_BENEFICIARY_SUBTYPES,
)
from shared.types.planner import PlannedTask, TaskParameters


def _build_context_read_fallback_task(
    subtype: str,
    message_text: str,
    *,
    account_action_override: str | None = None,
) -> PlannedTask | None:
    """Build a read-only worker task when context-read should not answer directly."""
    if subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
        action = account_action_override or "list_accounts"
        if action == "list":
            action = "list_accounts"
        risk: Literal["READ_ONLY", "MUTATION"] = "READ_ONLY"
        if action in {"link", "unlink", "set_default"}:
            risk = "MUTATION"
        return PlannedTask(
            task_id="t1",
            action=action,
            executor="account",
            instruction=message_text,
            parameters=TaskParameters(),
            risk=risk,
        )

    if subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
        return PlannedTask(
            task_id="t1",
            action="list_beneficiaries",
            executor="beneficiary",
            instruction=message_text,
            parameters=TaskParameters(),
            risk="READ_ONLY",
        )

    return None


__all__ = ["_build_context_read_fallback_task"]
