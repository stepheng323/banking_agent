"""Missing-field filtering for execution input prompts."""

from apps.chat.src.agent.orchestrator.workflows.execution.common import EXECUTION_ONLY_FIELDS
from apps.chat.src.agent.orchestrator.workflows.execution.runtime import ExecutionAccumulator
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _focus_beneficiary_ambiguity(
    *,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> None:
    beneficiary_blockers = [
        tid for tid, fields in agg.missing_fields_by_task.items() if "beneficiary_id" in set(fields)
    ]
    if not beneficiary_blockers:
        return

    focused_beneficiary_tid = next(
        (tid for tid in current_wave if tid in beneficiary_blockers),
        beneficiary_blockers[0],
    )
    for tid in list(agg.missing_fields_by_task):
        if tid != focused_beneficiary_tid:
            agg.missing_fields_by_task.pop(tid, None)
            agg.prompts_by_task.pop(tid, None)
            agg.details_by_task.pop(tid, None)
    agg.missing_fields_by_task[focused_beneficiary_tid] = ["beneficiary_id"]
    logger.info(
        "beneficiary_ambiguity_blocking_mode",
        focused_task_id=focused_beneficiary_tid,
        suppressed_count=max(len(beneficiary_blockers) - 1, 0),
    )


def _suppress_execution_only_prompts(agg: ExecutionAccumulator) -> None:
    has_basic_blocker = any(
        any(field not in EXECUTION_ONLY_FIELDS for field in fields) for fields in agg.missing_fields_by_task.values()
    )
    if not has_basic_blocker:
        return

    suppressed_tasks = [
        tid
        for tid, fields in agg.missing_fields_by_task.items()
        if all(field in EXECUTION_ONLY_FIELDS for field in fields)
    ]
    for tid in suppressed_tasks:
        del agg.missing_fields_by_task[tid]
        logger.info("suppressed_execution_prompt", task_id=tid, reason="basic_blocker_active")


def apply_missing_field_prompt_filters(
    *,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> None:
    _focus_beneficiary_ambiguity(current_wave=current_wave, agg=agg)
    _suppress_execution_only_prompts(agg)


def tasks_needing_basic_fields(agg: ExecutionAccumulator) -> list[str]:
    return [
        tid
        for tid in agg.missing_fields_by_task
        if any(field not in EXECUTION_ONLY_FIELDS for field in agg.missing_fields_by_task[tid])
    ]


__all__ = ["apply_missing_field_prompt_filters", "tasks_needing_basic_fields"]
