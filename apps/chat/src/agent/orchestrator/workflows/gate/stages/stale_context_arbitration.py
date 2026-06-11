"""Semantic-first arbitration for stale context follow-ups."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.outcomes import planner_handoff
from apps.chat.src.agent.orchestrator.workflows.gate.stages.contextual_followup_stages import (
    _candidate_locales,
    _looks_like_contextual_worker_acknowledgement,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import _could_be_schedule_read_request
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_router_stage import _stage_semantic_router
from apps.chat.src.agent.orchestrator.workflows.gate.support_identity import (
    _recent_batch_identity,
    _support_user_id,
)
from banking.support.context_manager import SupportContextManager
from banking.support.models import ReceiptBatchThreadState, SupportContext
from banking.support.reference_selection import (
    is_receipt_selection_message,
    is_strict_receipt_selector_message,
    is_strict_reference_selector_message,
)
from banking.transactions.runtime.async_group_recent_batch import get_recent_batch_reference
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_FRAME_SELECTOR_RE = re.compile(
    r"(?iu)^(?:"
    r"more|next|previous|prev|back|details?|show(?:\s+(?:it|this|them|those|again))?|"
    r"fetch(?:\s+(?:it|this|them|those|again))?|"
    r"refresh(?:\s+(?:it|this|them|those|again))?|"
    r"check(?:\s+(?:it|this|them|those))?\s+againo?|"
    r"(?:first|second|third|fourth|fifth|last)(?:\s+one)?|[1-5](?:st|nd|rd|th)?"
    r")$"
)
_MEDIA_TRANSFER_SIGNAL_RE = re.compile(
    r"(?im)^\s*User caption/instruction:\s*(?:send|transfer|pay|remit)\b|"
    r"\bCaption-derived transfer fields:\b"
)


@dataclass(frozen=True, slots=True)
class StaleContextSnapshot:
    """Stale context surfaces that can steal an unrelated fresh turn."""

    support_user_id: str | None = None
    support_context: SupportContext | None = None
    has_pending_reference: bool = False
    has_receipt_thread: bool = False
    has_support_transaction_context: bool = False
    has_recent_batch_reference: bool = False
    latest_context_frame: ContextFrame | None = None

    @property
    def has_context_frame(self) -> bool:
        return self.latest_context_frame is not None

    @property
    def has_support_context(self) -> bool:
        return bool(
            self.has_pending_reference
            or self.has_receipt_thread
            or self.has_support_transaction_context
            or self.has_recent_batch_reference
        )

    @property
    def is_active(self) -> bool:
        return self.has_support_context or self.has_context_frame


def _normalized_selector_text(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "").strip()).strip(" \t\r\n.,;:!?\"'()[]{}")


async def detect_stale_context(ctx: GateContext) -> StaleContextSnapshot:
    """Detect stale support/receipt/frame context that may bias routing."""
    support_user_id = _support_user_id(ctx.state_view)
    support_context: SupportContext | None = None
    has_recent_batch_reference = False

    if ctx.redis_client is not None:
        support_context = await SupportContextManager(ctx.redis_client).get(support_user_id)
        try:
            recent_batch = await get_recent_batch_reference(
                ctx.redis_client,
                identity=_recent_batch_identity(ctx.state_view),
            )
        except Exception as exc:  # pragma: no cover - defensive around Redis/test doubles
            logger.warning("recent_batch_reference_probe_failed", error=str(exc))
            recent_batch = None
        has_recent_batch_reference = bool(recent_batch and recent_batch.get("legs"))

    latest_frame = ContextFrameManager().latest_active_frame(ctx.state)
    receipt_thread = getattr(support_context, "receipt_thread_state", None)

    return StaleContextSnapshot(
        support_user_id=support_user_id,
        support_context=support_context,
        has_pending_reference=bool(getattr(support_context, "pending_reference", None)),
        has_receipt_thread=isinstance(receipt_thread, ReceiptBatchThreadState) and bool(receipt_thread.candidates),
        has_support_transaction_context=bool(getattr(support_context, "last_transaction_ref", None)),
        has_recent_batch_reference=has_recent_batch_reference,
        latest_context_frame=latest_frame,
    )


def is_strict_context_selector(message: str, snapshot: StaleContextSnapshot) -> bool:
    """Return true when deterministic stale-context routing is safe."""
    normalized = _normalized_selector_text(message)
    if not normalized or not snapshot.is_active:
        return False
    if snapshot.has_pending_reference and is_strict_reference_selector_message(normalized):
        return True
    has_receipt_context = (
        snapshot.has_receipt_thread or snapshot.has_recent_batch_reference or snapshot.has_support_transaction_context
    )
    if has_receipt_context:
        if is_strict_receipt_selector_message(normalized):
            return True
        if (
            snapshot.has_recent_batch_reference
            and is_receipt_selection_message(normalized)
            and not _looks_like_fresh_non_receipt_turn(normalized)
        ):
            return True
    return bool(snapshot.has_context_frame and _CONTEXT_FRAME_SELECTOR_RE.fullmatch(normalized))


async def clear_ephemeral_support_context(user_id: str, redis_client: Any | None) -> None:
    """Clear stale support fields without deleting useful long-lived support context."""
    if redis_client is None:
        return
    manager = SupportContextManager(redis_client)
    support_ctx = await manager.get(user_id)
    if support_ctx.pending_reference is None and support_ctx.receipt_thread_state is None:
        return
    support_ctx.pending_reference = None
    support_ctx.receipt_thread_state = None
    await manager.save(user_id, support_ctx)
    logger.info("stale_ephemeral_support_context_cleared", user_id=user_id)


def _add_stale_context_hints(ctx: GateContext, snapshot: StaleContextSnapshot) -> None:
    if snapshot.has_pending_reference:
        ctx.add_routing_hint(domain="support", reason="active_support_pending_reference", source="stale_context")
    if snapshot.has_receipt_thread:
        ctx.add_routing_hint(domain="support", reason="active_receipt_thread", source="stale_context")
    if snapshot.has_support_transaction_context:
        ctx.add_routing_hint(domain="support", reason="recent_support_transaction", source="stale_context")
    if snapshot.has_recent_batch_reference:
        ctx.add_routing_hint(domain="support", reason="recent_batch_receipt_reference", source="stale_context")
    if snapshot.has_context_frame:
        frame_type = snapshot.latest_context_frame.frame_type.value if snapshot.latest_context_frame else "unknown"
        ctx.add_routing_hint(
            domain="context_frame",
            reason=f"active_context_frame:{frame_type}",
            source="stale_context",
        )


def _looks_like_fresh_domain_turn(message_text: str) -> bool:
    return bool(
        _obvious_mixed_transaction_executors(message_text)
        or _classify_obvious_transfer_request(message_text)
        or _is_obvious_airtime_request(message_text)
        or _is_obvious_data_request(message_text)
        or _is_account_balance_request(message_text)
        or _is_account_domain_request(message_text)
        or _is_beneficiary_domain_request(message_text)
        or _is_query_domain_request(message_text)
        or _could_be_schedule_read_request(message_text)
    )


def _looks_like_fresh_context_frame_domain_turn(message_text: str) -> bool:
    """Return true for fresh turns that must preempt a stale visible frame.

    Fresh query requests are intentionally excluded because the context-frame
    stage already declines them and the deterministic query guard can handle
    them without an extra semantic-router call.
    """
    return bool(
        _obvious_mixed_transaction_executors(message_text)
        or _classify_obvious_transfer_request(message_text)
        or _is_obvious_airtime_request(message_text)
        or _is_obvious_data_request(message_text)
        or _is_account_balance_request(message_text)
        or _is_account_domain_request(message_text)
        or _is_beneficiary_domain_request(message_text)
        or _could_be_schedule_read_request(message_text)
    )


def _looks_like_fresh_non_receipt_turn(message_text: str) -> bool:
    return bool(
        _MEDIA_TRANSFER_SIGNAL_RE.search(message_text)
        or _obvious_mixed_transaction_executors(message_text)
        or _classify_obvious_transfer_request(message_text)
        or _is_obvious_airtime_request(message_text)
        or _is_obvious_data_request(message_text)
        or _is_account_balance_request(message_text)
        or _is_account_domain_request(message_text)
        or _is_beneficiary_domain_request(message_text)
        or _could_be_schedule_read_request(message_text)
    )


def _should_arbitrate(ctx: GateContext, snapshot: StaleContextSnapshot) -> bool:
    if snapshot.has_support_context:
        if _looks_like_contextual_worker_acknowledgement(ctx.message_text, _candidate_locales(ctx)):
            return False
        return True
    return snapshot.has_context_frame and _looks_like_fresh_context_frame_domain_turn(ctx.message_text)


def _semantic_updates_start_non_support_flow(updates: dict[str, Any]) -> bool:
    target_domain = updates.get("routing_target_domain")
    routing_owner = updates.get("routing_owner")
    routing_decision = updates.get("routing_decision")
    if target_domain == "support" or routing_decision == "domain_support":
        return False
    if isinstance(target_domain, str) and target_domain:
        return True
    return routing_owner == "planner" or routing_decision in {
        "planner_handoff",
        "planner_mixed",
        "planner_ambiguous",
        "support_hint_planner_handoff",
    }


async def _stage_stale_context_arbitration(ctx: GateContext) -> dict[str, Any] | None:
    """Let semantic routing arbitrate non-terse turns while stale context exists."""
    if ctx.live_pending_interrupt or ctx.task_planner is None:
        return None

    snapshot = await detect_stale_context(ctx)
    if not snapshot.is_active:
        return None
    if is_strict_context_selector(ctx.message_text, snapshot):
        logger.info(
            "gate_stale_context_arbitration_strict_selector",
            has_support_context=snapshot.has_support_context,
            has_context_frame=snapshot.has_context_frame,
        )
        return None
    if not _should_arbitrate(ctx, snapshot):
        return None

    _add_stale_context_hints(ctx, snapshot)
    logger.info(
        "gate_stale_context_arbitration_semantic_router",
        has_pending_reference=snapshot.has_pending_reference,
        has_receipt_thread=snapshot.has_receipt_thread,
        has_support_transaction_context=snapshot.has_support_transaction_context,
        has_recent_batch_reference=snapshot.has_recent_batch_reference,
        has_context_frame=snapshot.has_context_frame,
    )
    updates = await _stage_semantic_router(ctx)
    if updates is None:
        logger.warning("gate_stale_context_arbitration_semantic_router_unresolved")
        if snapshot.support_user_id and snapshot.has_support_context:
            await clear_ephemeral_support_context(snapshot.support_user_id, ctx.redis_client)
        return planner_handoff(ctx)

    if snapshot.support_user_id and snapshot.has_support_context and _semantic_updates_start_non_support_flow(updates):
        await clear_ephemeral_support_context(snapshot.support_user_id, ctx.redis_client)
    return updates


__all__ = [
    "StaleContextSnapshot",
    "clear_ephemeral_support_context",
    "detect_stale_context",
    "is_strict_context_selector",
    "_stage_stale_context_arbitration",
]
