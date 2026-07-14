"""Domain dispatch handler for the semantic router."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import (
    planner_handoff,
    policy_block,
    task_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import TRANSACTION_EXECUTORS
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import (
    _build_direct_schedule_read_updates,
    _semantic_schedule_response_mode,
)
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from banking.intent.routing_signals import (
    looks_like_transaction_replay_modifier_request,
)
from shared.types.planner import RouterDomainIntent, SemanticRoutingMode
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_ROUTE_TO_DOMAIN: dict[str, RouterDomainIntent] = {
    "domain_query": "query",
    "domain_account": "account",
    "domain_support": "support",
    "domain_beneficiary": "beneficiary",
    "domain_transfer": "transfer",
    "domain_airtime": "airtime",
    "domain_data": "data",
    "domain_faq": "faq",
}


async def _handle_semantic_domain_dispatch(
    ctx: GateContext,
    *,
    route: Any,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: SemanticRoutingMode | None,
) -> RouteResolution | None:
    if canonical_decision == "domain_schedule":
        schedule_response_mode = _semantic_schedule_response_mode(route)
        if schedule_response_mode is not None:
            logger.info(
                "gate_semantic_router_schedule_direct",
                decision=canonical_decision,
                mode=canonical_mode,
                schedule_response_mode=schedule_response_mode,
            )
            return _build_direct_schedule_read_updates(
                ctx,
                updates=updates,
                schedule_response_mode=schedule_response_mode,
                canonical_decision=canonical_decision,
                canonical_mode=canonical_mode,
                source="semantic_router",
            )
        logger.info(
            "gate_semantic_router_schedule_planner_handoff",
            decision=canonical_decision,
            mode=canonical_mode,
        )
        return planner_handoff(
            ctx,
            owner="semantic_router",
            decision="planner_handoff",
            target_domain="schedule",
            mode=canonical_mode,
            source="semantic_router",
            path_shape="semantic_router_schedule_planner_handoff",
            extra_updates=updates,
        )

    if canonical_decision not in _ROUTE_TO_DOMAIN:
        return None

    domain = _ROUTE_TO_DOMAIN[canonical_decision]
    if domain == "support" and looks_like_transaction_replay_modifier_request(ctx.message_text):
        logger.info(
            "gate_semantic_router_support_replay_modifier_veto",
            decision=canonical_decision,
            mode=canonical_mode,
        )
        if block_message := _direct_domain_capability_block_message(ctx.state_view, "transfer"):
            logger.info(
                "gate_semantic_router_replay_modifier_transfer_policy_blocked",
                decision=canonical_decision,
                mode=canonical_mode,
            )
            return policy_block(
                ctx,
                response=block_message,
                owner="guardrail",
                decision="capability_blocked",
                target_domain="transfer",
                mode=canonical_mode,
                source="semantic_router_veto",
                path_shape="transaction_replay_modifier_transfer_policy_blocked",
                heuristic_type="negative_guard",
                heuristic_name="transaction_replay_modifier",
                extra_updates={**(ctx.summary_updates or {}), **updates},
            )
        if await ctx.has_active_query_session():
            updates.update(
                _build_query_session_exit_updates(
                    ctx.state,
                )
            )
        task_id, spec = _build_direct_domain_task(
            state_view=ctx.state_view,
            domain="transfer",
            mode=canonical_mode,
        )
        return task_dispatch(
            ctx,
            tasks={task_id: spec},
            waves=[[task_id]],
            owner="guardrail",
            decision="transaction_replay_modifier_transfer",
            target_domain="transfer",
            mode=canonical_mode,
            source="semantic_router_veto",
            path_shape="transaction_replay_modifier_transfer",
            heuristic_type="negative_guard",
            heuristic_name="transaction_replay_modifier",
            extra_updates={**(ctx.summary_updates or {}), **updates},
        )

    mixed_executors = _obvious_mixed_transaction_executors(ctx.message_text)
    if domain in TRANSACTION_EXECUTORS and mixed_executors:
        updates["preplanner_expected_transaction_executors"] = mixed_executors
        logger.info(
            "gate_semantic_router_mixed_veto",
            decision=canonical_decision,
            attempted_domain=domain,
            expected_executors=mixed_executors,
        )
        return planner_handoff(
            ctx,
            owner="semantic_router",
            decision="planner_handoff",
            mode=canonical_mode,
            source="semantic_router_mixed_veto",
            path_shape="semantic_router_mixed_planner_handoff",
            extra_updates=updates,
        )

    if block_message := _direct_domain_capability_block_message(ctx.state_view, domain):
        logger.info(
            "gate_semantic_router_domain_policy_blocked",
            decision=canonical_decision,
            domain=domain,
            mode=canonical_mode,
        )
        return policy_block(
            ctx,
            response=block_message,
            owner="semantic_router",
            decision="capability_blocked",
            target_domain=domain,
            mode=canonical_mode,
            source="semantic_router",
            path_shape="semantic_router_domain_policy_blocked",
            extra_updates={**(ctx.summary_updates or {}), **updates},
        )

    if domain != "query" and await ctx.has_active_query_session():
        updates.update(
            _build_query_session_exit_updates(
                ctx.state,
            )
        )

    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain=domain,
        mode=canonical_mode,
    )
    logger.info(
        "gate_semantic_router_domain_dispatch",
        decision=canonical_decision,
        domain=domain,
        mode=canonical_mode,
        task_id=task_id,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="semantic_router",
        decision=canonical_decision,
        target_domain=domain,
        mode=canonical_mode,
        source="semantic_router",
        path_shape="semantic_router_domain",
        extra_updates={**(ctx.summary_updates or {}), **updates},
    )
