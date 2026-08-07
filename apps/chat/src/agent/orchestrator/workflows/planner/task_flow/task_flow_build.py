from typing import Any

from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from apps.chat.src.agent.orchestrator.workflows.planner.core.domains import TRANSACTION_EXECUTORS
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerQualityReport
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_capabilities import (
    _filter_capability_blocked_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_limits import (
    _transaction_batch_limit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_postprocessing import (
    _postprocess_planner_tasks_with_quality,
)
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_planner_task_updates(
    *,
    planner_output: PlannerOutput,
    planner_quality_report: PlannerQualityReport | None = None,
    text: str,
    locale: str,
    query_session_source: str | None,
    query_session_snapshot: dict[str, Any] | None,
    expected_transaction_task_count: int = 0,
) -> dict[str, Any]:
    postprocess_result = _postprocess_planner_tasks_with_quality(
        planner_output,
        text,
        quality_report=planner_quality_report,
    )
    planner_output = postprocess_result.planner_output
    planner_quality_report = postprocess_result.quality_report
    planner_output, blocked_messages = _filter_capability_blocked_tasks(planner_output, locale)
    capability_policy_notice = "\n\n".join(blocked_messages) if blocked_messages else None
    if blocked_messages and not planner_output.tasks:
        return {
            "planner_output": planner_output,
            "new_tasks": {},
            "waves": [],
            "pending_query_clarification_update": None,
            "capability_block_response": capability_policy_notice,
            "capability_policy_notice": None,
            "planner_quality_report": planner_quality_report,
        }

    batch_limit_updates = _transaction_batch_limit_updates(
        planner_output=planner_output,
        capability_policy_notice=capability_policy_notice,
    )
    if batch_limit_updates is not None:
        batch_limit_updates["planner_quality_report"] = planner_quality_report
        return batch_limit_updates

    expected_count = max(0, int(expected_transaction_task_count or 0))
    transaction_task_count = sum(
        1 for task in planner_output.tasks if task.executor in TRANSACTION_EXECUTORS
    )
    if expected_count > 1 and transaction_task_count < expected_count:
        # The gate's count is typed evidence from the accepted turn, not a
        # second text parser.  Never let an under-produced plan reach a
        # confirmation surface: doing so could submit only part of a batch.
        logger.warning(
            "planner_transaction_task_count_mismatch",
            expected_count=expected_count,
            actual_count=transaction_task_count,
        )
        return {
            "planner_output": planner_output,
            "new_tasks": {},
            "waves": [],
            "pending_query_clarification_update": None,
            "planner_incomplete_response": True,
            "capability_policy_notice": capability_policy_notice,
            "planner_quality_report": planner_quality_report.with_reason(
                "planner.transaction_task_count_mismatch"
            ),
        }

    new_tasks, waves = build_task_specs_and_waves_from_plan_items(
        planner_output.tasks,
        text,
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )
    materialized_transaction_task_count = sum(
        1 for task in new_tasks.values() if task.type in TRANSACTION_EXECUTORS
    )
    if expected_count > 1 and materialized_transaction_task_count < expected_count:
        # Dict-backed task state cannot represent duplicate planner ids.  Do
        # not let the last duplicate silently overwrite an earlier money
        # movement and present a partial confirmation; return the same safe
        # retry path used for an under-produced typed plan.
        logger.warning(
            "planner_transaction_task_materialization_mismatch",
            expected_count=expected_count,
            planned_count=transaction_task_count,
            materialized_count=materialized_transaction_task_count,
        )
        return {
            "planner_output": planner_output,
            "new_tasks": {},
            "waves": [],
            "pending_query_clarification_update": None,
            "planner_incomplete_response": True,
            "capability_policy_notice": capability_policy_notice,
            "planner_quality_report": planner_quality_report.with_reason(
                "planner.transaction_task_materialization_mismatch"
            ),
        }
    logger.info(
        "planner_task_plan_materialized",
        expected_transaction_task_count=expected_count,
        transaction_task_count=materialized_transaction_task_count,
        task_count=len(new_tasks),
        wave_count=len(waves),
    )
    del query_session_source, query_session_snapshot
    return {
        "planner_output": planner_output,
        "new_tasks": new_tasks,
        "waves": waves,
        "pending_query_clarification_update": None,
        "capability_policy_notice": capability_policy_notice,
        "planner_quality_report": planner_quality_report,
    }


__all__ = ["_build_planner_task_updates"]
