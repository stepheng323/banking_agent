import json
from typing import Any

from apps.core.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_domain_task,
    _has_explicit_cancel,
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _next_direct_account_task_id,
    _next_direct_beneficiary_task_id,
    _resolve_beneficiary_suggestion_reply,
    _route_observability_updates,
)

from apps.core.src.agent.orchestrator.models.domain import (
    TaskSpec,
    TaskStage,
)
from apps.core.src.agent.orchestrator.nodes.cancellation import build_cancellation_reset_updates
from apps.core.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_beneficiary_suggestion(ctx: GateContext) -> dict[str, Any] | None:
    """Beneficiary save/dismiss from Redis suggestion."""
    if ctx.live_pending_interrupt or not ctx.redis_client:
        return None

    suggestion_key = f"user:{ctx.state.phone_number}:beneficiary_suggestion"
    try:
        suggestion_data = await ctx.redis_client.get(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_lookup_failed", error=str(exc))
        suggestion_data = None

    if not suggestion_data:
        return None

    suggestion_payload: dict[str, Any] | None
    try:
        parsed_payload = json.loads(suggestion_data)
        suggestion_payload = parsed_payload if isinstance(parsed_payload, dict) else None
    except Exception:
        suggestion_payload = None

    decision = _resolve_beneficiary_suggestion_reply(
        ctx.message_text,
        locale=ctx.current_locale,
        suggestion_payload=suggestion_payload,
    )
    logger.info(
        "beneficiary_suggestion_gate_decision",
        decision=decision.action,
        reason=decision.reason,
        locale=ctx.current_locale,
        alias_present=bool(decision.alias),
    )
    if decision.action in {"save_default", "save_alias"}:
        task_id = _next_direct_beneficiary_task_id(ctx.state.tasks)
        task_payload: dict[str, Any] = {
            "action": "save_beneficiary",
            "instruction": ctx.state.last_message_text,
            "message": ctx.state.last_message_text,
        }
        if decision.alias:
            task_payload["alias"] = decision.alias
        spec = TaskSpec(
            id=task_id,
            type="beneficiary",
            stage=TaskStage.DRAFT,
            payload=task_payload,
        )
        return {
            **ctx.gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            **_route_observability_updates(
                owner="guardrail",
                decision="beneficiary_save",
                target_domain="beneficiary",
                mode="new",
            ),
        }

    try:
        await ctx.redis_client.delete(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
    else:
        logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)
    return None


async def _stage_balance_direct(ctx: GateContext) -> dict[str, Any] | None:
    """Direct balance check shortcut."""
    if ctx.live_pending_interrupt or not ctx.phrase_heavy_fastpath_allowed:
        return None
    if not _is_account_balance_request(ctx.message_text):
        return None
    cleanup_updates: dict[str, Any] = {}
    if _has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id = _next_direct_account_task_id(ctx.state.tasks)
    spec = TaskSpec(
        id=task_id,
        type="account",
        stage=TaskStage.DRAFT,
        payload={
            "action": "check_balance",
            "message": ctx.state.last_message_text,
            "instruction": ctx.state.last_message_text,
        },
    )
    logger.info("gate_direct_account_balance", task_id=task_id, with_cleanup=bool(cleanup_updates))
    return {
        **ctx.gate_updates,
        **cleanup_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        **_route_observability_updates(
            owner="guardrail",
            decision="balance_direct",
            target_domain="account",
            mode="new",
        ),
    }


async def _stage_account_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic account domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_account_domain_request(ctx.message_text)
    ):
        return None
    cleanup_updates: dict[str, Any] = {}
    if _has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="account", mode="new")
    logger.info("gate_deterministic_account_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        **cleanup_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_account_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_account_domain",
            target_domain="account",
            mode="new",
        ),
    }


async def _stage_beneficiary_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic beneficiary domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_beneficiary_domain_request(ctx.message_text)
    ):
        return None
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="beneficiary", mode="new")
    logger.info("gate_deterministic_beneficiary_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_beneficiary_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_beneficiary_domain",
            target_domain="beneficiary",
            mode="new",
        ),
    }


async def _stage_airtime_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic airtime domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_airtime_request(ctx.message_text)
    ):
        return None
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="airtime", mode="new")
    logger.info("gate_deterministic_airtime_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_airtime_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_airtime_domain",
            target_domain="airtime",
            mode="new",
        ),
    }


async def _stage_data_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic data domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_data_request(ctx.message_text)
    ):
        return None
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="data", mode="new")
    logger.info("gate_deterministic_data_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_data_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_data_domain",
            target_domain="data",
            mode="new",
        ),
    }
