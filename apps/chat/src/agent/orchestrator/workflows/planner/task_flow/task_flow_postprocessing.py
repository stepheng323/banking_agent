from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import (
    PlannerPostprocessResult,
    PlannerQualityReport,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_repair import (
    _validate_and_repair_planner_clauses,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_flow import (
    _strip_transactional_depends_on_edges,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_expand import (
    _expand_underproduced_transfer_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_reconcile import (
    _reconcile_multi_transfer_recipient_tasks,
)
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _clause_text_by_index(planner_output: PlannerOutput) -> dict[int, str]:
    return {
        int(clause.clause_index): str(clause.text or "").strip()
        for clause in getattr(planner_output, "clauses", [])
        if getattr(clause, "clause_index", None)
    }


def _transfer_clause_indexes(planner_output: PlannerOutput) -> list[int]:
    return [
        int(clause.clause_index)
        for clause in getattr(planner_output, "clauses", [])
        if str(getattr(clause, "intent_family", "") or "").strip().lower().replace("-", "_").replace(" ", "_")
        == "transfer"
    ]


def _attach_single_transfer_clause_index(planner_output: PlannerOutput) -> PlannerOutput:
    transfer_clause_indexes = _transfer_clause_indexes(planner_output)
    if len(transfer_clause_indexes) != 1:
        return planner_output

    planner_output.tasks = [
        task
        if task.executor != "transfer" or task.source_clause_index
        else task.model_copy(update={"source_clause_index": transfer_clause_indexes[0]})
        for task in planner_output.tasks
    ]
    return planner_output


def _postprocess_planner_tasks_with_quality(
    planner_output: PlannerOutput,
    text: str,
    *,
    quality_report: PlannerQualityReport | None = None,
) -> PlannerPostprocessResult:
    quality_report = quality_report or PlannerQualityReport()
    planner_output = _attach_single_transfer_clause_index(planner_output)
    clause_text_by_index = _clause_text_by_index(planner_output)
    fanout_tasks, fanout_meta = _expand_underproduced_transfer_tasks(
        planner_output.tasks,
        text,
        clause_text_by_index=clause_text_by_index,
    )
    if fanout_meta:
        planner_output.tasks = fanout_tasks
        planner_output.is_complex = True
        if fanout_meta.get("fanout_mode") == "multi_recipient":
            quality_report = quality_report.with_reason("postprocess.transfer.text_derived_fanout")
        logger.info(
            "planner_transfer_multi_recipient_fanout_applied",
            source_task_id=fanout_meta["source_task_id"],
            recipient_count=fanout_meta["recipient_count"],
            recipient_names=fanout_meta["recipient_names"],
        )

    reconciled_tasks, reconcile_meta = _reconcile_multi_transfer_recipient_tasks(planner_output.tasks, text)
    if reconcile_meta:
        planner_output.tasks = reconciled_tasks
        quality_report = quality_report.with_reason("postprocess.transfer.recipient_reconcile")
        logger.info(
            "planner_transfer_multi_recipient_reconcile_applied",
            recipient_count=reconcile_meta["recipient_count"],
            recipient_names=reconcile_meta["recipient_names"],
            changed_tasks=reconcile_meta["changed_tasks"],
        )

    normalized_tasks, stripped_edges = _strip_transactional_depends_on_edges(planner_output.tasks)
    if stripped_edges:
        planner_output.tasks = normalized_tasks
        logger.info(
            "txn_dep_removed_for_batch_auth",
            source_task_ids=sorted({source for source, _ in stripped_edges}),
            target_task_ids=sorted({target for _, target in stripped_edges}),
            removed_edges=[f"{source}->{target}" for source, target in stripped_edges],
            removed_count=len(stripped_edges),
        )

    repaired_output, repair_meta = _validate_and_repair_planner_clauses(planner_output)
    if repair_meta:
        planner_output = repaired_output
        quality_report = quality_report.with_reason("postprocess.clause_repair")
        logger.info(
            "planner_clause_validation_repair_applied",
            repaired_balance_clause_indexes=repair_meta["repaired_balance_clause_indexes"],
            repaired_transfer_clause_indexes=repair_meta["repaired_transfer_clause_indexes"],
            changed_transfer_task_ids=repair_meta["changed_transfer_task_ids"],
        )

    return PlannerPostprocessResult(planner_output=planner_output, quality_report=quality_report)


def _postprocess_planner_tasks(planner_output: PlannerOutput, text: str) -> PlannerOutput:
    return _postprocess_planner_tasks_with_quality(planner_output, text).planner_output


__all__ = ["_postprocess_planner_tasks", "_postprocess_planner_tasks_with_quality"]
