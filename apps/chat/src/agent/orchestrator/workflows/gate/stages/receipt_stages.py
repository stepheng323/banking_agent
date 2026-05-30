from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.receipt_requests import (
    _has_receipt_thread_candidates,
    _looks_like_receipt_request,
    _looks_like_receipt_selector_followup,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.support_identity import (
    _recent_batch_identity_for_state,
    _support_user_id_for_state,
)
from apps.chat.src.agent.workers.support.context_manager import SupportContextManager
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


async def _stage_receipt_thread_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Active receipt thread support dispatch."""
    if (
        ctx.live_pending_interrupt
        or not ctx.redis_client
        or _is_captioned_media_transfer_request(ctx.message_text)
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
    if (
        ctx.live_pending_interrupt
        or not ctx.redis_client
        or _is_captioned_media_transfer_request(ctx.message_text)
        or not _looks_like_receipt_request(ctx.message_text)
    ):
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
