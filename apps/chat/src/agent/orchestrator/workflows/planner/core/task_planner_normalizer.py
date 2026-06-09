"""Deterministic post-processing for planner transaction tasks.

This layer improves one-shot extraction completeness without extra LLM calls.
It only patches missing transaction parameters when parsing is unambiguous.
"""

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_batch import (
    collapse_transfer_batch_tasks_with_meta,
    normalize_transfer_only_primary_intent,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_bills import (
    normalize_airtime_params,
    normalize_data_params,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_clauses import (
    normalize_planner_clauses,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_parsing import (
    parse_amount_value,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_transfer import (
    normalize_transfer_amount_field,
    normalize_transfer_params,
    repair_account_aware_transfer_params,
    repair_source_first_transfer_params,
    sanitize_transfer_explicit_split,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerQualityReport
from shared.types.planner import (
    AirtimeTaskParameters,
    BaseTaskParameters,
    DataTaskParameters,
    PlannerOutput,
    TransferTaskParameters,
    copy_task_parameters,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_TX_EXECUTORS = {"transfer", "airtime", "data"}


def _normalizer_reason(executor: str, field: str) -> str:
    return f"normalizer.{executor}.{field}"


def _has_valid_recipient_allocations(params: TransferTaskParameters) -> bool:
    allocations = params.recipient_allocations or []
    if len(allocations) < 2:
        return False
    return all(allocation.recipient_name and parse_amount_value(allocation.amount) for allocation in allocations)


def _is_mechanical_numeric_amount_patch(
    raw_params: BaseTaskParameters,
    normalized_params: BaseTaskParameters,
) -> bool:
    raw_amount = getattr(raw_params, "amount", None)
    if not isinstance(raw_amount, str):
        return False
    if not raw_amount.strip().replace(".", "", 1).isdigit():
        return False
    raw_numeric = parse_amount_value(raw_amount)
    normalized_numeric = parse_amount_value(getattr(normalized_params, "amount", None))
    return raw_numeric is not None and raw_numeric == normalized_numeric


def _is_mechanical_phone_alias_patch(
    raw_params: BaseTaskParameters,
    normalized_params: BaseTaskParameters,
    field: str,
) -> bool:
    if not isinstance(raw_params, AirtimeTaskParameters | DataTaskParameters):
        return False
    if not isinstance(normalized_params, AirtimeTaskParameters | DataTaskParameters):
        return False

    if field == "phone":
        raw_recipient_phone = getattr(raw_params, "recipient_phone", None)
        return bool(raw_recipient_phone) and getattr(normalized_params, "phone", None) == raw_recipient_phone

    if field == "recipient_phone":
        raw_phone = getattr(raw_params, "phone", None)
        return bool(raw_phone) and getattr(normalized_params, "recipient_phone", None) == raw_phone

    return False


def _quality_relevant_patched_fields(
    raw_params: BaseTaskParameters,
    normalized_params: BaseTaskParameters,
    patched_fields: list[str],
) -> list[str]:
    relevant_fields: list[str] = []
    for field in patched_fields:
        if field == "amount" and (
            _is_mechanical_numeric_amount_patch(raw_params, normalized_params)
            or (isinstance(raw_params, TransferTaskParameters) and _has_valid_recipient_allocations(raw_params))
        ):
            continue
        if _is_mechanical_phone_alias_patch(raw_params, normalized_params, field):
            continue
        relevant_fields.append(field)
    return relevant_fields


def normalize_planner_transaction_output_with_quality(
    planner_output: PlannerOutput,
    user_text: str,
) -> tuple[PlannerOutput, PlannerQualityReport]:
    """Patch missing transaction parameters with deterministic, precision-first parsing."""
    quality_report = PlannerQualityReport()
    planner_output = normalize_planner_clauses(planner_output)
    if not planner_output.tasks:
        return planner_output, quality_report

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

        raw_params = copy_task_parameters(task.parameters, executor=task.executor, action=task.action)
        params = raw_params.model_copy(deep=True)
        patched_fields: list[str] = []
        ambiguous_fields: list[str] = []

        if task.executor == "transfer" and isinstance(params, TransferTaskParameters):
            patched_fields.extend(normalize_transfer_amount_field(params))
            patched_fields.extend(repair_source_first_transfer_params(params, source_text))
            transfer_patched, ambiguous_fields = normalize_transfer_params(params, source_text)
            patched_fields.extend(transfer_patched)
            patched_fields.extend(repair_account_aware_transfer_params(params, source_text))
            patched_fields.extend(sanitize_transfer_explicit_split(params))
        elif task.executor == "airtime" and isinstance(params, AirtimeTaskParameters):
            patched_fields, ambiguous_fields = normalize_airtime_params(params, source_text)
        elif task.executor == "data" and isinstance(params, DataTaskParameters):
            patched_fields, ambiguous_fields = normalize_data_params(params, source_text)

        if patched_fields:
            applied_count += 1
            quality_fields = _quality_relevant_patched_fields(raw_params, params, patched_fields)
            quality_report = quality_report.with_reasons(
                [_normalizer_reason(str(task.executor), field) for field in quality_fields]
            )
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
    normalized_output, batch_collapsed = collapse_transfer_batch_tasks_with_meta(normalized_output, user_text)
    if batch_collapsed:
        quality_report = quality_report.with_reason("normalizer.transfer.batch_collapse")
    normalized_output = normalize_transfer_only_primary_intent(normalized_output)
    return normalized_output, quality_report


def normalize_planner_transaction_output(planner_output: PlannerOutput, user_text: str) -> PlannerOutput:
    normalized_output, _quality_report = normalize_planner_transaction_output_with_quality(planner_output, user_text)
    return normalized_output
