"""Decision normalization helpers for context-frame follow-up handling."""

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from shared.types.planner import ContextFrameFollowupDecision

CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE = 0.55


def frame_domain(frame_type: ContextFrameType) -> str | None:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "account"
    if frame_type == ContextFrameType.SCHEDULE_LIST:
        return "schedule"
    if frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL, ContextFrameType.RECEIPT}:
        return "query"
    return None


def decision_target_text(decision: ContextFrameFollowupDecision) -> str:
    return (decision.target_text or "").strip()


def decision_rank_text(decision: ContextFrameFollowupDecision) -> str | None:
    return decision.rank


def decision_field_text(decision: ContextFrameFollowupDecision) -> str | None:
    return decision.requested_field


def canonical_decision(decision: str) -> str:
    aliases = {
        "completeness_check": "answer_completeness",
        "entity_lookup": "lookup_entity",
        "detail_request": "show_details",
        "selection": "select_item",
        "replay": "replay_tasks",
        "new_task": "start_new_task",
    }
    return aliases.get(decision, decision)


__all__ = [
    "CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE",
    "canonical_decision",
    "decision_field_text",
    "decision_rank_text",
    "decision_target_text",
    "frame_domain",
]
