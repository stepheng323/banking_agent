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
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _is_obvious_airtime_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
    _next_direct_account_task_id,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_balance_direct(ctx: GateContext) -> dict[str, Any] | None:
    """Direct balance check shortcut."""
    if ctx.live_pending_interrupt or not ctx.phrase_heavy_fastpath_allowed:
        return None
    if not _is_account_balance_request(ctx.message_text):
        return None
    cleanup_updates: dict[str, Any] = {}
    if has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id = _next_direct_account_task_id(ctx.state_view.tasks)
    spec = TaskSpec(
        id=task_id,
        type="account",
        stage=TaskStage.DRAFT,
        payload={
            "action": "check_balance",
            "message": ctx.state_view.last_message_text,
            "instruction": ctx.state_view.last_message_text,
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
        "semantic_path_shape": "balance_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="balance_direct",
            target_domain="account",
            mode="new",
            route_source="account_balance_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="balance_request",
        ),
    }


async def _stage_account_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic account domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_account_domain_request(ctx.message_text)
    ):
        return None
    cleanup_updates: dict[str, Any] = {}
    if has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="account", mode="new")
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
            route_source="account_domain_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="account_domain_request",
        ),
    }


async def _stage_beneficiary_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic beneficiary domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_beneficiary_domain_request(ctx.message_text)
    ):
        return None
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="beneficiary", mode="new")
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
            route_source="beneficiary_domain_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="beneficiary_list_request",
        ),
    }


async def _stage_airtime_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic airtime domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_airtime_request(ctx.message_text)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "airtime"):
        logger.info("gate_deterministic_airtime_domain_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_airtime_domain_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="airtime",
                mode="new",
                route_source="airtime_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name="obvious_airtime_request",
            ),
        }
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="airtime", mode="new")
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
            route_source="airtime_domain_guard",
            heuristic_type="slot_parser",
            heuristic_name="obvious_airtime_request",
        ),
    }
