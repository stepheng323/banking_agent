from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import TRANSACTION_EXECUTORS
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_QUERY_SESSION_STASH_KEYS = (
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


def _stash_query_session_for_transaction_switch(
    *,
    query_session_source: str | None,
    query_session_snapshot: dict[str, Any] | None,
    planned_tasks: list[Any],
) -> dict[str, Any] | None:
    if (
        query_session_source != "redis"
        or not query_session_snapshot
        or not bool(query_session_snapshot.get("session_active"))
        or not any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in planned_tasks)
    ):
        return None

    stashed_query_session_update = {
        key: query_session_snapshot.get(key) for key in _QUERY_SESSION_STASH_KEYS if key in query_session_snapshot
    }
    stashed_query_session_update["session_active"] = True
    logger.info(
        "planner_query_session_stashed_for_transaction_switch",
        keys=list(stashed_query_session_update.keys()),
    )
    return stashed_query_session_update


__all__ = ["_stash_query_session_for_transaction_switch"]
