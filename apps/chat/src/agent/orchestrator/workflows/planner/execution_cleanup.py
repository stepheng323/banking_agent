"""Cleanup side effects after planner execution."""

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def _clear_stale_beneficiary_suggestion(
    *,
    state_view: PlannerStateView,
    planner_context: str,
    planner_output: PlannerOutput,
    redis_client: redis.Redis | None,
) -> None:
    if not (redis_client and planner_context != "None" and planner_output and planner_output.tasks):
        return

    is_saving = any(t.executor == "beneficiary" and t.action == "save_beneficiary" for t in planner_output.tasks)
    if is_saving:
        return

    suggestion_key = f"user:{state_view.phone_number}:beneficiary_suggestion"
    await redis_client.delete(suggestion_key)
    logger.info("cleared_stale_beneficiary_context", phone_hash=log_fingerprint(state_view.phone_number))


__all__ = ["_clear_stale_beneficiary_suggestion"]
