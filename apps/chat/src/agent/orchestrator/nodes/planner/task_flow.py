"""Planner task assembly and postprocessing flow helpers."""

from typing import Any

from apps.chat.src.agent.orchestrator.nodes.planner.context_read import TRANSACTION_EXECUTORS
from apps.chat.src.agent.orchestrator.nodes.planner.policy import _filter_capability_blocked_tasks
from apps.chat.src.agent.orchestrator.nodes.planner.postprocess import (
    _expand_underproduced_transfer_tasks,
    _reconcile_multi_transfer_recipient_tasks,
    _strip_transactional_depends_on_edges,
    _validate_and_repair_planner_clauses,
)
from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from shared.policy.transaction_limits import MAX_TRANSACTION_BATCH_TASKS
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _transaction_batch_limit_message(*, transaction_count: int) -> str:
    return (
        f"I can handle up to {MAX_TRANSACTION_BATCH_TASKS} transactions in one batch. "
        f"You asked for {transaction_count}. Please send the first {MAX_TRANSACTION_BATCH_TASKS} now, "
        "then I can help with the rest."
    )


async def _build_planner_task_updates(
    *,
    planner_output: Any,
    text: str,
    locale: str,
    query_session_source: str | None,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    clause_text_by_index = {
        int(clause.clause_index): str(clause.text or "").strip()
        for clause in getattr(planner_output, "clauses", [])
        if getattr(clause, "clause_index", None)
    }
    transfer_clause_indexes = [
        int(clause.clause_index)
        for clause in getattr(planner_output, "clauses", [])
        if str(getattr(clause, "intent_family", "") or "").strip().lower().replace("-", "_").replace(" ", "_")
        == "transfer"
    ]
    if len(transfer_clause_indexes) == 1:
        planner_output.tasks = [
            task
            if task.executor != "transfer" or task.source_clause_index
            else task.model_copy(update={"source_clause_index": transfer_clause_indexes[0]})
            for task in planner_output.tasks
        ]
    fanout_tasks, fanout_meta = _expand_underproduced_transfer_tasks(
        planner_output.tasks,
        text,
        clause_text_by_index=clause_text_by_index,
    )
    if fanout_meta:
        planner_output.tasks = fanout_tasks
        planner_output.is_complex = True
        logger.info(
            "planner_transfer_multi_recipient_fanout_applied",
            source_task_id=fanout_meta["source_task_id"],
            recipient_count=fanout_meta["recipient_count"],
            recipient_names=fanout_meta["recipient_names"],
        )

    reconciled_tasks, reconcile_meta = _reconcile_multi_transfer_recipient_tasks(planner_output.tasks, text)
    if reconcile_meta:
        planner_output.tasks = reconciled_tasks
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
        logger.info(
            "planner_clause_validation_repair_applied",
            repaired_balance_clause_indexes=repair_meta["repaired_balance_clause_indexes"],
            changed_transfer_task_ids=repair_meta["changed_transfer_task_ids"],
        )

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

    transaction_task_count = sum(
        1 for task in planner_output.tasks if getattr(task, "executor", None) in TRANSACTION_EXECUTORS
    )
    if transaction_task_count > MAX_TRANSACTION_BATCH_TASKS:
        logger.info(
            "planner_transaction_batch_limit_blocked",
            transaction_task_count=transaction_task_count,
            max_transaction_batch_tasks=MAX_TRANSACTION_BATCH_TASKS,
        )
        return {
            "planner_output": planner_output,
            "new_tasks": {},
            "waves": [],
            "stashed_query_session_update": None,
            "batch_limit_response": _transaction_batch_limit_message(transaction_count=transaction_task_count),
            "capability_policy_notice": capability_policy_notice,
        }

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
        "capability_policy_notice": capability_policy_notice,
    }


__all__ = ["_build_planner_task_updates"]
