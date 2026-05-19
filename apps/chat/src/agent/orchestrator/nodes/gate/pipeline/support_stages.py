import re
from typing import Any

from apps.chat.src.agent.graphs.support.context_manager import SupportContextManager
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
    _has_receipt_thread_candidates,
    _looks_like_receipt_request,
    _looks_like_receipt_selector_followup,
    _recent_batch_identity_for_state,
    _route_observability_updates,
    _support_user_id_for_state,
)
from apps.chat.src.agent.shared.routing_signals import (
    looks_like_support_problem_statement,
    looks_like_transaction_replay_modifier_request,
)
from shared.services.async_completion import get_recent_batch_reference
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
    support_ctx = await SupportContextManager(ctx.redis_client).get(_support_user_id_for_state(ctx.state))
    if not _looks_like_support_context_followup(ctx.message_text, support_ctx):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "support"):
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

    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="support")
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
        or ctx.state.has_quote
        or not callable(getattr(ctx.task_planner, "route_semantic_turn", None))
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


async def _stage_receipt_thread_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Active receipt thread support dispatch."""
    if (
        ctx.live_pending_interrupt
        or not ctx.redis_client
        or not _looks_like_receipt_selector_followup(ctx.message_text)
    ):
        return None
    support_ctx = await SupportContextManager(ctx.redis_client).get(_support_user_id_for_state(ctx.state))
    receipt_thread_state = getattr(support_ctx, "receipt_thread_state", None)
    if not _has_receipt_thread_candidates(receipt_thread_state):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "support"):
        logger.info("gate_receipt_thread_support_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "support_receipt_thread_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="support",
            ),
        }
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="support")
    spec.payload["intent"] = "receipt_request"
    spec.payload["recent_batch_followup"] = True
    spec.payload["receipt_thread_followup"] = True
    logger.info(
        "gate_receipt_thread_support_handoff",
        task_id=task_id,
        async_group_id=receipt_thread_state.async_group_id,
    )
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "support_receipt_thread_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="receipt_thread_support",
            target_domain="support",
        ),
    }


async def _stage_receipt_request(ctx: GateContext) -> dict[str, Any] | None:
    """Recent batch receipt request."""
    if ctx.live_pending_interrupt or not ctx.redis_client or not _looks_like_receipt_request(ctx.message_text):
        return None
    recent_batch_identity = _recent_batch_identity_for_state(ctx.state)
    recent_batch = await get_recent_batch_reference(ctx.redis_client, identity=recent_batch_identity)
    if recent_batch is None or not recent_batch.get("legs"):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "support"):
        logger.info("gate_recent_batch_receipt_support_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "support_receipt_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="support",
            ),
        }
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="support")
    spec.payload["intent"] = "receipt_request"
    spec.payload["recent_batch_followup"] = True
    logger.info(
        "gate_recent_batch_receipt_support_handoff",
        task_id=task_id,
        async_group_id=recent_batch.get("async_group_id"),
        identity=recent_batch_identity,
    )
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "support_receipt_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="recent_batch_receipt_support",
            target_domain="support",
        ),
    }
