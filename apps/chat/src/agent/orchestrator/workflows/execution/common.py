"""Shared execution-node helpers and constants."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.control_state import execution_control_state
from banking.accounts.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TERMINAL_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}
BLOCKING_DEPENDENCY_STAGES = {TaskStage.FAILED, TaskStage.CANCELLED}
EXECUTION_ONLY_FIELDS = {"source_account_id", "pin", "confirmation_summary"}
INPUT_MUTABLE_STAGES = {
    TaskStage.DRAFT,
    TaskStage.EXTRACTED,
    TaskStage.RESOLVED,
    TaskStage.VALIDATED,
}
TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}


def _is_read_only_data_plan_query(task: Any) -> bool:
    return task.type == "data" and task.payload.get("action") == "data_plan_query"


def _build_mandate_gate_error(accounts: list[dict], locale: str) -> str:
    """Build mandate error using contextual transfer destinations when available."""
    normalized_accounts = [account for account in accounts if isinstance(account, dict)]
    return build_pending_mandate_message(normalized_accounts, locale)


def _with_policy_notice(state: OrchestratorState, outbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend policy notice once per turn when present."""
    control = execution_control_state(state)
    if not control.has_policy_notice:
        return outbox
    logger.info("policy_notice_injected")
    return control.prepend_policy_notice(outbox)


def _dependency_resolution(task: Any, all_tasks: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve whether task dependencies are ready, waiting, or failed."""
    depends_on = task.depends_on if hasattr(task, "depends_on") else []
    for dep_id in depends_on:
        dep_task = all_tasks.get(dep_id)
        if not dep_task:
            continue
        if dep_task.stage in BLOCKING_DEPENDENCY_STAGES:
            return "cancel", dep_id
        if dep_task.stage != TaskStage.COMPLETED:
            return "wait", dep_id
    return "ready", None


__all__ = [
    "EXECUTION_ONLY_FIELDS",
    "INPUT_MUTABLE_STAGES",
    "TERMINAL_STAGES",
    "TRANSACTION_TASK_TYPES",
    "_build_mandate_gate_error",
    "_dependency_resolution",
    "_is_read_only_data_plan_query",
    "_with_policy_notice",
]
