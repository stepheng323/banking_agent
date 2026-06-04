import re
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.support_identity import _support_user_id
from banking.intent.routing_signals import (
    looks_like_support_problem_statement,
    looks_like_transaction_replay_modifier_request,
)
from banking.support.context_manager import SupportContextManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_SUPPORT_CONTEXT_REFERENCE_RE = re.compile(
    r"^\s*(?:my|the|this|that)?\s*(?:last|latest|most\s+recent|recent)\s+"
    r"(?:transaction|transfer|payment)\s*$"
    r"|^\s*(?:the\s+)?(?:last|latest|recent)\s+one\s*$"
    r"|^\s*(?:it|this|that|that\s+one)\s*$"
    r"|^\s*\d{1,2}\s*$",
    re.IGNORECASE,
)
_SUPPORT_CONTEXT_ACTION_RE = re.compile(
    r"\b(?:details?|full\s+details?|more\s+info(?:rmation)?|status|retry|try\s+again|resend|"
    r"send\s+again|receipt|proof\s+of\s+payment|payment\s+proof)\b",
    re.IGNORECASE,
)
_SUPPORT_CONTEXT_EXPLICIT_LATEST_STATUS_QUERY_RE = re.compile(
    r"\b(?:what(?:'s| is)|whats|check|show|get|tell\s+me)\b.*\bstatus\s+of\s+"
    r"(?:my\s+)?(?:last|latest|most\s+recent)\s+(?:transaction|transfer|payment)\b"
    r"|\b(?:my\s+)?(?:last|latest|most\s+recent)\s+(?:transaction|transfer|payment)\s+status\b",
    re.IGNORECASE,
)


def _looks_like_support_issue_request(message_text: str) -> bool:
    return looks_like_support_problem_statement(message_text)


def _looks_like_support_context_followup(message_text: str, support_ctx: Any) -> bool:
    normalized = re.sub(r"\s+", " ", (message_text or "").strip())
    if not normalized:
        return False
    if looks_like_transaction_replay_modifier_request(normalized):
        return False
    pending_reference = getattr(support_ctx, "pending_reference", None)
    last_support_step = getattr(support_ctx, "last_support_step", None)
    last_transaction_ref = getattr(support_ctx, "last_transaction_ref", None)

    if pending_reference is not None and (
        _SUPPORT_CONTEXT_REFERENCE_RE.search(normalized) or _SUPPORT_CONTEXT_ACTION_RE.search(normalized)
    ):
        return True
    if last_support_step == "asked_for_reference" and _SUPPORT_CONTEXT_REFERENCE_RE.search(normalized):
        return True
    if (
        last_transaction_ref
        and _SUPPORT_CONTEXT_ACTION_RE.search(normalized)
        and not _SUPPORT_CONTEXT_EXPLICIT_LATEST_STATUS_QUERY_RE.search(normalized)
    ):
        return True
    return False


async def _stage_support_context_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Route active support clarification/detail follow-ups back to support."""
    if ctx.live_pending_interrupt or not ctx.redis_client:
        return None
    support_ctx = await SupportContextManager(ctx.redis_client).get(_support_user_id(ctx.state_view))
    if not _looks_like_support_context_followup(ctx.message_text, support_ctx):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "support"):
        logger.info("gate_support_context_followup_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "support_context_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="support",
                route_source="support_context",
                heuristic_type="guardrail_shortcut",
                heuristic_name="active_support_context",
            ),
        }

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    logger.info("gate_support_context_followup_handoff", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "support_context_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="support_context_followup",
            target_domain="support",
            route_source="support_context",
            heuristic_type="guardrail_shortcut",
            heuristic_name="active_support_context",
        ),
    }


async def _stage_support_issue_request(ctx: GateContext) -> dict[str, Any] | None:
    """Attach a non-authoritative support hint for common transaction/ticket issue phrases."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or ctx.task_planner is None
        or not _looks_like_support_issue_request(ctx.message_text)
    ):
        return None
    ctx.add_routing_hint(
        domain="support",
        reason="transaction_or_ticket_issue_phrase",
        source="support_issue_phrase",
    )
    logger.info("gate_support_issue_hint_attached")
    return None
