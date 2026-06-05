"""Deterministic post-processing for planner transaction tasks.

This layer improves one-shot extraction completeness without extra LLM calls.
It only patches missing transaction parameters when parsing is unambiguous.
"""

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_batch import (
    collapse_transfer_batch_tasks,
    normalize_transfer_only_primary_intent,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_bills import (
    normalize_airtime_params,
    normalize_data_params,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_clauses import (
    normalize_planner_clauses,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_transfer import (
    normalize_transfer_amount_field,
    normalize_transfer_params,
    repair_account_aware_transfer_params,
    sanitize_transfer_explicit_split,
)
from shared.types.planner import PlannerOutput, TaskParameters
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_TX_EXECUTORS = {"transfer", "airtime", "data"}


def normalize_planner_transaction_output(planner_output: PlannerOutput, user_text: str) -> PlannerOutput:
    """Patch missing transaction parameters with deterministic, precision-first parsing."""
    planner_output = normalize_planner_clauses(planner_output)
    if not planner_output.tasks:
        return planner_output

    locale = planner_output.detected_language or "unknown"
    updated_tasks = []
    applied_count = 0

    for task in planner_output.tasks:
        if task.executor not in _TX_EXECUTORS:
            updated_tasks.append(task)
            continue

        source_text = (task.instruction or user_text or "").strip()
        if not source_text:
            updated_tasks.append(task)
            continue

        params = task.parameters.model_copy(deep=True) if task.parameters else TaskParameters()
        patched_fields: list[str] = []
        ambiguous_fields: list[str] = []

        if task.executor == "transfer":
            patched_fields.extend(normalize_transfer_amount_field(params))
            transfer_patched, ambiguous_fields = normalize_transfer_params(params, source_text)
            patched_fields.extend(transfer_patched)
            patched_fields.extend(repair_account_aware_transfer_params(params, source_text))
            patched_fields.extend(sanitize_transfer_explicit_split(params))
        elif task.executor == "airtime":
            patched_fields, ambiguous_fields = normalize_airtime_params(params, source_text)
        elif task.executor == "data":
            patched_fields, ambiguous_fields = normalize_data_params(params, source_text)

        if patched_fields:
            applied_count += 1
            logger.info(
                "planner_task_normalizer_applied",
                task_id=task.task_id,
                executor=task.executor,
                locale=locale,
                normalized_fields=patched_fields,
            )
            updated_tasks.append(task.model_copy(update={"parameters": params}))
        else:
            updated_tasks.append(task)

        if ambiguous_fields:
            logger.info(
                "planner_task_normalizer_ambiguous_skip",
                task_id=task.task_id,
                executor=task.executor,
                locale=locale,
                ambiguous_fields=ambiguous_fields,
            )

    if applied_count:
        logger.info(
            "planner_task_normalizer_summary",
            locale=locale,
            normalized_task_count=applied_count,
            total_tasks=len(planner_output.tasks),
        )

    normalized_output = planner_output.model_copy(update={"tasks": updated_tasks})
    normalized_output = collapse_transfer_batch_tasks(normalized_output, user_text)
    return normalize_transfer_only_primary_intent(normalized_output)
