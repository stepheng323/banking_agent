"""Planner task assembly and postprocessing flow helpers."""

from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context_read import TRANSACTION_EXECUTORS
from apps.core.src.agent.orchestrator.nodes.planner_postprocess import (
    _expand_underproduced_transfer_tasks,
    _strip_transactional_depends_on_edges,
)
from apps.core.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_planner_task_updates(
    *,
    state: OrchestratorState,
    planner_output: Any,
    text: str,
    query_session_source: str | None,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    fanout_tasks, fanout_meta = _expand_underproduced_transfer_tasks(planner_output.tasks, text)
    if fanout_meta:
        planner_output.tasks = fanout_tasks
        planner_output.is_complex = True
        logger.info(
            "planner_transfer_multi_recipient_fanout_applied",
            source_task_id=fanout_meta["source_task_id"],
            recipient_count=fanout_meta["recipient_count"],
            recipient_names=fanout_meta["recipient_names"],
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

    stashed_query_session_update: dict[str, Any] | None = None
    if (
        query_session_source == "redis"
        and query_session_snapshot
        and bool(query_session_snapshot.get("session_active"))
        and any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in planner_output.tasks)
    ):
        stash_keys = (
            "session_active",
            "query_contract",
            "query_result",
            "surface",
            "show_expanded",
            "current_page",
            "page_size",
            "account_id",
            "account_ids",
            "cached_transactions",
            "cache_fetched_at",
            "cache_fingerprint",
            "timestamp",
        )
        stashed_query_session_update = {
            key: query_session_snapshot.get(key) for key in stash_keys if key in query_session_snapshot
        }
        stashed_query_session_update["session_active"] = True
        logger.info(
            "planner_query_session_stashed_for_transaction_switch",
            keys=list(stashed_query_session_update.keys()),
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
    }


__all__ = ["_build_planner_task_updates"]
