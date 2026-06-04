from typing import Any

from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_capabilities import (
    _filter_capability_blocked_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_limits import (
    _transaction_batch_limit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_postprocessing import (
    _postprocess_planner_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_query_session import (
    _stash_query_session_for_transaction_switch,
)
from shared.types.planner import PlannerOutput


async def _build_planner_task_updates(
    *,
    planner_output: PlannerOutput,
    text: str,
    locale: str,
    query_session_source: str | None,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    planner_output = _postprocess_planner_tasks(planner_output, text)
    planner_output, blocked_messages = _filter_capability_blocked_tasks(planner_output, locale)
    capability_policy_notice = "\n\n".join(blocked_messages) if blocked_messages else None
    if blocked_messages and not planner_output.tasks:
        return {
            "planner_output": planner_output,
            "new_tasks": {},
            "waves": [],
            "stashed_query_session_update": None,
            "capability_block_response": capability_policy_notice,
            "capability_policy_notice": None,
        }

    batch_limit_updates = _transaction_batch_limit_updates(
        planner_output=planner_output,
        capability_policy_notice=capability_policy_notice,
    )
    if batch_limit_updates is not None:
        return batch_limit_updates

    stashed_query_session_update = _stash_query_session_for_transaction_switch(
        query_session_source=query_session_source,
        query_session_snapshot=query_session_snapshot,
        planned_tasks=planner_output.tasks,
    )
    new_tasks, waves = build_task_specs_and_waves_from_plan_items(
        planner_output.tasks,
        text,
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )
    return {
        "planner_output": planner_output,
        "new_tasks": new_tasks,
        "waves": waves,
        "stashed_query_session_update": stashed_query_session_update,
        "capability_policy_notice": capability_policy_notice,
    }


__all__ = ["_build_planner_task_updates"]
