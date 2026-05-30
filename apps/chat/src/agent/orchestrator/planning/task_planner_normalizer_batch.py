"""Batch-transfer normalization for planner task output."""

from decimal import Decimal

from apps.chat.src.agent.orchestrator.planning.task_planner_normalizer_parsing import parse_amount_value
from shared.money import MoneyAmount
from shared.types.planner import PlannedTask, PlannerOutput, RecipientAllocation, TaskParameters
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _has_batch_transfer_cue(user_text: str) -> bool:
    lowered = (user_text or "").lower()
    return any(cue in lowered for cue in (" each ", " split ", " between ", " btw "))


def _single_recipient_allocation(params: TaskParameters) -> RecipientAllocation | None:
    allocations = params.recipient_allocations or []
    if len(allocations) != 1:
        return None
    allocation = allocations[0]
    recipient_name = str(allocation.recipient_name or "").strip()
    amount = allocation.amount
    if not recipient_name or amount <= 0:
        return None
    return RecipientAllocation(recipient_name=recipient_name, amount=amount)


def _collapse_transfer_target(params: TaskParameters) -> tuple[str, MoneyAmount] | None:
    allocation = _single_recipient_allocation(params)
    if allocation is not None:
        return allocation.recipient_name, allocation.amount

    recipient_name = str(params.recipient_name or params.recipient or "").strip()
    amount = parse_amount_value(params.amount)
    if not recipient_name or amount is None or amount <= 0:
        return None
    return recipient_name, amount


def _can_collapse_transfer_task(task: PlannedTask) -> bool:
    if task.executor != "transfer" or task.action != "send_money" or task.depends_on:
        return False

    params = task.parameters or TaskParameters()
    if params.transfer_all or params.transfer_percentage is not None:
        return False
    if params.reference is not None:
        return False
    if (
        params.explicit_split
        or params.source_accounts
        or (params.recipient_allocations and len(params.recipient_allocations) != 1)
    ):
        return False
    if params.recipient_account or params.bank_name or params.recipient_phone or params.phone:
        return False
    if (
        params.network
        or params.plan
        or params.schedule
        or params.scheduled
        or params.schedule_id
        or params.schedule_selector
    ):
        return False
    if params.recurring or params.international or params.alias or params.is_self:
        return False
    if params.source_bank_name or params.source_account_index is not None or params.use_dual_accounts is not None:
        return False

    return _collapse_transfer_target(params) is not None


def collapse_transfer_batch_tasks(planner_output: PlannerOutput, user_text: str) -> PlannerOutput:
    if not _has_batch_transfer_cue(user_text):
        return planner_output

    tasks = planner_output.tasks
    if len(tasks) < 2:
        return planner_output

    collapsed_tasks: list[PlannedTask] = []
    id_rewrites: dict[str, str] = {}
    applied_count = 0
    idx = 0

    while idx < len(tasks):
        task = tasks[idx]
        if not _can_collapse_transfer_task(task):
            collapsed_tasks.append(task)
            idx += 1
            continue

        run_end = idx + 1
        while run_end < len(tasks) and _can_collapse_transfer_task(tasks[run_end]):
            run_end += 1

        run = tasks[idx:run_end]
        if len(run) < 2:
            collapsed_tasks.append(task)
            idx = run_end
            continue

        base_task = run[0].model_copy(deep=True)
        allocations: list[RecipientAllocation] = []
        total_amount = Decimal("0.00")
        for child in run:
            params = child.parameters or TaskParameters()
            target = _collapse_transfer_target(params)
            if target is None:
                allocations = []
                break
            recipient_name, amount = target
            allocations.append(RecipientAllocation(recipient_name=recipient_name, amount=amount))
            total_amount += amount

        if len(allocations) != len(run):
            collapsed_tasks.extend(run)
            idx = run_end
            continue

        base_params = base_task.parameters.model_copy(deep=True) if base_task.parameters else TaskParameters()
        base_params.recipient = None
        base_params.recipient_name = None
        base_params.amount = total_amount
        base_params.recipient_allocations = allocations
        base_task.parameters = base_params
        base_task.instruction = user_text

        collapsed_tasks.append(base_task)
        for child in run[1:]:
            id_rewrites[child.task_id] = base_task.task_id
        applied_count += 1
        idx = run_end

    if not applied_count:
        return planner_output

    rewritten_tasks: list[PlannedTask] = []
    for task in collapsed_tasks:
        if not task.depends_on:
            rewritten_tasks.append(task)
            continue
        updated_depends_on: list[str] = []
        seen: set[str] = set()
        for dep in task.depends_on:
            rewritten = id_rewrites.get(dep, dep)
            if rewritten in seen:
                continue
            seen.add(rewritten)
            updated_depends_on.append(rewritten)
        rewritten_tasks.append(task.model_copy(update={"depends_on": updated_depends_on}))

    logger.info(
        "planner_transfer_batch_collapsed_to_allocations",
        collapsed_run_count=applied_count,
        collapsed_task_count=len(id_rewrites) + applied_count,
        locale=planner_output.detected_language or "unknown",
    )
    return planner_output.model_copy(update={"tasks": rewritten_tasks})


def normalize_transfer_only_primary_intent(planner_output: PlannerOutput) -> PlannerOutput:
    if planner_output.primary_intent != "mixed":
        return planner_output
    if not planner_output.tasks:
        return planner_output
    if any(task.executor != "transfer" for task in planner_output.tasks):
        return planner_output
    logger.info(
        "planner_transfer_only_primary_intent_normalized",
        task_count=len(planner_output.tasks),
        previous_primary_intent="mixed",
        normalized_primary_intent="transfer",
    )
    return planner_output.model_copy(update={"primary_intent": "transfer"})
