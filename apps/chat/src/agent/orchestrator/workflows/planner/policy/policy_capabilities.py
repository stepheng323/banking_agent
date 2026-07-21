"""Runtime capability filtering for planner tasks."""

from banking.policy.service import capability_block_message
from banking.runtime.operations import operation_spec
from shared.types.planner import PlannedTask, PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_DEFAULT_ACTIONS = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
}

SCHEDULE_ACTIONS = {
    "schedule_transfer",
    "recurring_transfer",
    "schedule_airtime",
    "recurring_airtime",
    "schedule_data",
    "recurring_data",
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
    "pause_scheduled_transaction",
    "resume_scheduled_transaction",
    "list_scheduled_runs",
    "find_scheduled_run",
}

POLICY_ACTION_ALIASES = {
    ("support", "handle_request"): "collect_details",
    ("support", "report_issue"): "collect_details",
}

PLANNER_POLICY_DEFAULT_ACTIONS = {
    **TRANSACTION_DEFAULT_ACTIONS,
    "schedule": "schedule_transfer",
    "support": "collect_details",
    "faq": "answer_question",
}


def _task_capability_target(task: PlannedTask) -> tuple[str, str] | None:
    executor = str(getattr(task, "executor", "") or "").strip()
    action = str(getattr(task, "action", "") or "").strip()
    parameters = getattr(task, "parameters", None)

    if (
        executor == "transfer"
        and action == "send_money"
        and (getattr(parameters, "schedule", None) or getattr(parameters, "scheduled", None))
    ):
        return "schedule", "schedule_transfer"
    if action:
        try:
            operation = operation_spec(executor, action)
        except ValueError:
            return None
        return operation.domain, operation.policy_action

    default_action = PLANNER_POLICY_DEFAULT_ACTIONS.get(executor)
    if default_action is None:
        return None
    return executor, default_action


def _filter_capability_blocked_tasks(
    planner_output: PlannerOutput, locale: str = "en"
) -> tuple[PlannerOutput, list[str]]:
    """Remove transaction tasks blocked by runtime capability policy."""
    tasks = list(getattr(planner_output, "tasks", []) or [])
    if not tasks:
        return planner_output, []

    kept_tasks = []
    blocked_messages: list[str] = []
    for task in tasks:
        executor = str(getattr(task, "executor", "") or "").strip()
        capability_target = _task_capability_target(task)
        if not capability_target:
            kept_tasks.append(task)
            continue
        capability_domain, capability_action = capability_target

        block_message = capability_block_message(domain=capability_domain, action=capability_action, locale=locale)
        if block_message:
            logger.info(
                "planner_task_capability_blocked",
                executor=executor,
                domain=capability_domain,
                action=capability_action,
            )
            if block_message not in blocked_messages:
                blocked_messages.append(block_message)
            continue
        kept_tasks.append(task)

    if len(kept_tasks) == len(tasks):
        return planner_output, []

    return planner_output.model_copy(update={"tasks": kept_tasks}), blocked_messages


__all__ = [
    "PLANNER_POLICY_DEFAULT_ACTIONS",
    "POLICY_ACTION_ALIASES",
    "SCHEDULE_ACTIONS",
    "TRANSACTION_DEFAULT_ACTIONS",
    "_filter_capability_blocked_tasks",
    "_task_capability_target",
]
