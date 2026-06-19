import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.receipt_requests import (
    _has_receipt_thread_candidates,
    _looks_like_receipt_request,
    _looks_like_receipt_selector_followup,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.support_identity import (
    _recent_batch_identity,
)
from banking.intent.routing_signals import (
    looks_like_support_problem_statement,
    looks_like_transaction_replay_modifier_request,
)
from banking.presentation.i18n.renderer import render_message
from banking.support.classifier import classify_support_intent_deterministic
from banking.support.models import ReceiptBatchThreadState
from banking.support.reference_selection import is_strict_reference_selector_message
from banking.transactions.runtime.async_group_recent_batch import get_recent_batch_reference
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_MEDIA_CAPTION_PREFIX = "User caption/instruction:"
_CAPTION_TRANSFER_REASONS = {
    "fresh_transfer_command",
    "fresh_transfer_missing_recipient_command",
    "batch_transfer_command",
    "account_aware_transfer_command",
}


def _is_captioned_media_transfer_request(message_text: str) -> bool:
    if _MEDIA_CAPTION_PREFIX not in (message_text or ""):
        return False
    return _classify_obvious_transfer_request(message_text) in _CAPTION_TRANSFER_REASONS


def _is_fresh_transaction_request(message_text: str) -> bool:
    return bool(
        _obvious_mixed_transaction_executors(message_text)
        or _classify_obvious_transfer_request(message_text)
        or _is_obvious_airtime_request(message_text)
        or _is_obvious_data_request(message_text)
    )


async def _stage_receipt_thread_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Active receipt thread support dispatch."""
    if (
        ctx.live_pending_interrupt
        or not ctx.redis_client
        or _is_fresh_transaction_request(ctx.message_text)
        or _is_captioned_media_transfer_request(ctx.message_text)
        or not _looks_like_receipt_selector_followup(ctx.message_text)
    ):
        return None
    support_ctx = await ctx.ensure_support_context()
    receipt_thread_state = getattr(support_ctx, "receipt_thread_state", None)
    if not isinstance(receipt_thread_state, ReceiptBatchThreadState) or not _has_receipt_thread_candidates(
        receipt_thread_state
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "support"):
        logger.info("gate_receipt_thread_support_policy_blocked")
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="support_receipt_thread_policy_blocked",
            target_domain="support",
        )
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    spec.payload["intent"] = "receipt_request"
    spec.payload["recent_batch_followup"] = True
    spec.payload["receipt_thread_followup"] = True
    logger.info(
        "gate_receipt_thread_support_handoff",
        task_id=task_id,
        async_group_id=receipt_thread_state.async_group_id,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="receipt_thread_support",
        semantic_path_shape="support_receipt_thread_direct",
        extra_updates={"pending_interrupt": None},
        target_domain="support",
    )


async def _stage_receipt_request(ctx: GateContext) -> dict[str, Any] | None:
    """Recent batch receipt request."""
    if (
        ctx.live_pending_interrupt
        or not ctx.redis_client
        or _is_captioned_media_transfer_request(ctx.message_text)
        or not _looks_like_receipt_request(ctx.message_text)
    ):
        return None
    recent_batch_identity = _recent_batch_identity(ctx.state_view)
    recent_batch = await get_recent_batch_reference(cast(Any, ctx.redis_client), identity=recent_batch_identity)
    if recent_batch is None or not recent_batch.get("legs"):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "support"):
        logger.info("gate_recent_batch_receipt_support_policy_blocked")
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="support_receipt_policy_blocked",
            target_domain="support",
        )
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    spec.payload["intent"] = "receipt_request"
    spec.payload["recent_batch_followup"] = True
    logger.info(
        "gate_recent_batch_receipt_support_handoff",
        task_id=task_id,
        async_group_id=recent_batch.get("async_group_id"),
        identity=recent_batch_identity,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="recent_batch_receipt_support",
        semantic_path_shape="support_receipt_direct",
        extra_updates={"pending_interrupt": None},
        target_domain="support",
    )


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

    if pending_reference is not None and is_strict_reference_selector_message(normalized):
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
    support_ctx = await ctx.ensure_support_context()
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
    if await ctx.defer_active_query_session_to_semantic_router(source="recent_transaction_support_guard"):
        logger.info("gate_recent_transaction_support_deferred_to_semantic_router_for_active_query")
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
    """Route common transaction/ticket issue phrases directly to support."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not _looks_like_support_issue_request(ctx.message_text)
    ):
        return None
    if await ctx.defer_active_query_session_to_semantic_router(source="support_issue_guard"):
        logger.info("gate_support_issue_deferred_to_semantic_router_for_active_query")
        return None

    if block_message := _direct_domain_capability_block_message(ctx.state_view, "support"):
        logger.info("gate_support_issue_policy_blocked")
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="support_issue_policy_blocked",
            target_domain="support",
            route_source="support_issue_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="support_issue_phrase",
        )

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="support")
    deterministic = classify_support_intent_deterministic(ctx.message_text)
    if deterministic is not None and deterministic.intent is not None and deterministic.confidence >= 0.85:
        spec.payload["intent"] = deterministic.intent.value
    logger.info("gate_support_issue_direct", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="support_issue_direct",
        semantic_path_shape="support_issue_direct",
        extra_updates={"pending_interrupt": None},
        target_domain="support",
        route_source="support_issue_guard",
        heuristic_type="guardrail_shortcut",
        heuristic_name="support_issue_phrase",
    )
