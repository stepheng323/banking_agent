"""Recent answer focus derivation for turn context summaries."""

import time

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView


def _derive_recent_answer_focus(state_view: PlannerStateView) -> str | None:
    now = int(time.time())
    for frame in reversed(state_view.context_frames):
        if (frame.created_at_ts + frame.ttl_seconds) <= now:
            continue
        if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            return "linked_accounts_summary"
        if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
            return "beneficiary_list"
        if frame.frame_type == ContextFrameType.TRANSACTION_LIST:
            return "query_results"
        if frame.frame_type == ContextFrameType.RECEIPT:
            return "receipt"

    planner_output = state_view.planner_output
    if planner_output and getattr(planner_output, "context_read_subtype", None):
        return str(planner_output.context_read_subtype)

    return None


__all__ = ["_derive_recent_answer_focus"]
