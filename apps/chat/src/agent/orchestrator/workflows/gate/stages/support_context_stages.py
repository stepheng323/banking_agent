import re
from typing import Any

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from apps.chat.src.agent.orchestrator.workflows.gate.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.support_identity import _support_user_id
from banking.intent.routing_signals import (
    looks_like_support_problem_statement,
    looks_like_transaction_replay_modifier_request,
)
from banking.presentation.i18n.renderer import render_message
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
_RECENT_TRANSACTION_REVERSAL_RE = re.compile(
    r"\b(?:reverse|reversal|refund|money\s+back)\b",
    re.IGNORECASE,
)
_RECENT_TRANSACTION_REFERENCE_RE = re.compile(
    r"\b(?:the|this|that|last|latest|recent|transaction|transfer|payment|it)\b",
    re.IGNORECASE,
)


def _looks_like_support_issue_request(message_text: str) -> bool:
    return looks_like_support_problem_statement(message_text)


def _looks_like_recent_transaction_reversal_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (message_text or "").strip())
    if not normalized:
        return False
    if looks_like_transaction_replay_modifier_request(normalized):
        return False
    return bool(
        _RECENT_TRANSACTION_REVERSAL_RE.search(normalized) and _RECENT_TRANSACTION_REFERENCE_RE.search(normalized)
    )


def _latest_single_transaction_entity(ctx: GateContext) -> ContextEntity | None:
    frame = ContextFrameManager().latest_active_frame(ctx.state)
    if frame is None:
        return None
    if frame.frame_type not in {ContextFrameType.RECEIPT, ContextFrameType.TRANSACTION_DETAIL}:
        return None
    if len(frame.items) != 1:
        return None
    entity = frame.items[0]
    if entity.entity_type != EntityType.TRANSACTION:
        return None
    return entity


def _latest_transaction_context_needs_clarification(ctx: GateContext) -> bool:
    frame = ContextFrameManager().latest_active_frame(ctx.state)
    if frame is None:
        return False
    if frame.frame_type not in {
        ContextFrameType.RECEIPT,
        ContextFrameType.TRANSACTION_DETAIL,
        ContextFrameType.TRANSACTION_LIST,
    }:
        return False
    transaction_count = sum(1 for item in frame.items if item.entity_type == EntityType.TRANSACTION)
    return transaction_count > 1


def _transaction_payload_from_entity(entity: ContextEntity) -> dict[str, Any]:
    transaction = dict(entity.data)
    reference = str(
        transaction.get("transaction_id")
        or transaction.get("id")
        or transaction.get("reference")
        or entity.entity_id
        or ""
    ).strip()
    if reference:
        transaction.setdefault("transaction_id", reference)
        transaction.setdefault("id", reference)
        transaction.setdefault("reference", reference)
    transaction.setdefault("transaction_type", transaction.get("type") or transaction.get("task_type") or "transfer")
    return transaction


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
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="support_context_policy_blocked",
            target_domain="support",
            route_source="support_context",
            heuristic_type="guardrail_shortcut",
            heuristic_name="active_support_context",
        )

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    logger.info("gate_support_context_followup_handoff", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="support_context_followup",
        semantic_path_shape="support_context_direct",
        extra_updates={"pending_interrupt": None},
        target_domain="support",
        route_source="support_context",
        heuristic_type="guardrail_shortcut",
        heuristic_name="active_support_context",
    )


async def _stage_recent_transaction_support_request(ctx: GateContext) -> dict[str, Any] | None:
    """Route reversal/refund follow-ups against a just-displayed transaction."""
    if ctx.live_pending_interrupt or ctx.state_view.has_quote:
        return None
    if not _looks_like_recent_transaction_reversal_request(ctx.message_text):
        return None
    entity = _latest_single_transaction_entity(ctx)
    if entity is None:
        if _latest_transaction_context_needs_clarification(ctx):
            logger.info("gate_recent_transaction_support_ambiguous")
            return direct_response(
                ctx,
                response=render_message("orchestrator.ambiguity.support_transaction", ctx.current_locale),
                owner="guardrail",
                decision="banking_coded_ambiguity_support",
                semantic_path_shape="banking_coded_ambiguity_clarify",
                route_source="context_frame",
                heuristic_type="guardrail_shortcut",
                heuristic_name="recent_transaction_reversal_ambiguity",
            )
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "support"):
        logger.info("gate_recent_transaction_support_policy_blocked")
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="recent_transaction_support_policy_blocked",
            target_domain="support",
            route_source="context_frame",
            heuristic_type="guardrail_shortcut",
            heuristic_name="recent_transaction_reversal",
        )

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    spec.payload["intent"] = "reversal_refund"
    spec.payload["transaction"] = _transaction_payload_from_entity(entity)
    logger.info("gate_recent_transaction_support_handoff", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="recent_transaction_support",
        semantic_path_shape="recent_transaction_support_direct",
        extra_updates={"pending_interrupt": None},
        target_domain="support",
        route_source="context_frame",
        heuristic_type="guardrail_shortcut",
        heuristic_name="recent_transaction_reversal",
    )


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
