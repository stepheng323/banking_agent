"""Missing-field filtering for execution input prompts."""

from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import EXECUTION_ONLY_FIELDS
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _focus_beneficiary_ambiguity(
    *,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> None:
    beneficiary_blockers = [tid for tid, fields in agg.input_request_items() if "beneficiary_id" in set(fields)]
    if not beneficiary_blockers:
        return

    focused_beneficiary_tid = next(
        (tid for tid in current_wave if tid in beneficiary_blockers),
        beneficiary_blockers[0],
    )
    logger.info(
        "beneficiary_ambiguity_focus_preserved",
        focused_task_id=focused_beneficiary_tid,
        sibling_input_count=max(agg.input_request_count() - 1, 0),
    )


def _suppress_execution_only_prompts(agg: ExecutionAccumulator) -> None:
    has_basic_blocker = any(
        any(field not in EXECUTION_ONLY_FIELDS for field in fields) for _, fields in agg.input_request_items()
    )
    if not has_basic_blocker:
        return

    suppressed_tasks = [
        tid for tid, fields in agg.input_request_items() if all(field in EXECUTION_ONLY_FIELDS for field in fields)
    ]
    for tid in suppressed_tasks:
        agg.remove_missing_fields(tid)
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
        tid for tid, fields in agg.input_request_items() if any(field not in EXECUTION_ONLY_FIELDS for field in fields)
    ]


__all__ = ["apply_missing_field_prompt_filters", "tasks_needing_basic_fields"]
