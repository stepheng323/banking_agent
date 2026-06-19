from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    has_explicit_cancel,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_generic_account_balance_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _is_obvious_airtime_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
    _next_direct_account_task_id,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_balance_direct(ctx: GateContext) -> dict[str, Any] | None:
    """Direct balance check shortcut."""
    if ctx.live_pending_interrupt or not ctx.phrase_heavy_fastpath_allowed:
        return None
    if not _is_account_balance_request(ctx.message_text):
        return None
    if await ctx.defer_active_query_session_to_semantic_router(source="balance_direct_guard"):
        logger.info("gate_balance_direct_deferred_to_semantic_router_for_active_query")
        return None
    cleanup_updates: dict[str, Any] = {}
    if has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id = _next_direct_account_task_id(ctx.state_view.tasks)
    payload: dict[str, Any] = {
        "action": "check_balance",
        "message": ctx.state_view.last_message_text,
        "instruction": ctx.state_view.last_message_text,
    }
    if _is_generic_account_balance_request(ctx.message_text):
        payload["skip_parse"] = True
    spec = TaskSpec(
        id=task_id,
        type="account",
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    logger.info("gate_direct_account_balance", task_id=task_id, with_cleanup=bool(cleanup_updates))
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="balance_direct",
        semantic_path_shape="balance_direct",
        extra_updates={**cleanup_updates, "pending_interrupt": None},
        target_domain="account",
        mode="new",
        route_source="account_balance_guard",
        heuristic_type="guardrail_shortcut",
        heuristic_name="balance_request",
    )


async def _stage_account_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic account domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_account_domain_request(ctx.message_text)
    ):
        return None
    if await ctx.defer_active_query_session_to_semantic_router(source="account_domain_guard"):
        logger.info("gate_account_domain_deferred_to_semantic_router_for_active_query")
        return None
    cleanup_updates: dict[str, Any] = {}
    if has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="account", mode="new")
    logger.info("gate_deterministic_account_domain", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="deterministic_account_domain",
        semantic_path_shape="deterministic_account_domain",
        extra_updates={**cleanup_updates, "pending_interrupt": None},
        target_domain="account",
        mode="new",
        route_source="account_domain_guard",
        heuristic_type="guardrail_shortcut",
        heuristic_name="account_domain_request",
    )


async def _stage_beneficiary_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic beneficiary domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_beneficiary_domain_request(ctx.message_text)
    ):
        return None
    if await ctx.defer_active_query_session_to_semantic_router(source="beneficiary_domain_guard"):
        logger.info("gate_beneficiary_domain_deferred_to_semantic_router_for_active_query")
        return None
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="beneficiary", mode="new")
    logger.info("gate_deterministic_beneficiary_domain", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="deterministic_beneficiary_domain",
        semantic_path_shape="deterministic_beneficiary_domain",
        extra_updates={"pending_interrupt": None},
        target_domain="beneficiary",
        mode="new",
        route_source="beneficiary_domain_guard",
        heuristic_type="guardrail_shortcut",
        heuristic_name="beneficiary_list_request",
    )


async def _stage_airtime_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic airtime domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_airtime_request(ctx.message_text)
    ):
        return None
    if await ctx.defer_active_query_session_to_semantic_router(source="airtime_domain_guard"):
        logger.info("gate_airtime_domain_deferred_to_semantic_router_for_active_query")
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "airtime"):
        logger.info("gate_deterministic_airtime_domain_policy_blocked")
        return direct_response(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            semantic_path_shape="deterministic_airtime_domain_policy_blocked",
            target_domain="airtime",
            mode="new",
            route_source="airtime_domain_guard",
            heuristic_type="slot_parser",
            heuristic_name="obvious_airtime_request",
        )
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="airtime", mode="new")
    logger.info("gate_deterministic_airtime_domain", task_id=task_id)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="deterministic_airtime_domain",
        semantic_path_shape="deterministic_airtime_domain",
        extra_updates={"pending_interrupt": None},
        target_domain="airtime",
        mode="new",
        route_source="airtime_domain_guard",
        heuristic_type="slot_parser",
        heuristic_name="obvious_airtime_request",
    )
