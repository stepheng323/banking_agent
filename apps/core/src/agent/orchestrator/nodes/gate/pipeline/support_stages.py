from typing import Any

from apps.core.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_domain_task,
    _has_receipt_thread_candidates,
    _looks_like_receipt_request,
    _looks_like_receipt_selector_followup,
    _recent_batch_identity_for_state,
    _route_observability_updates,
    _support_user_id_for_state,
)

from apps.core.src.agent.graphs.support.context_manager import SupportContextManager
from apps.core.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from shared.services.async_completion import get_recent_batch_reference
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
