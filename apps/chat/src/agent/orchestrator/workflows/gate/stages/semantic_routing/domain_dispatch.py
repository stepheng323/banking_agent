"""Domain dispatch handler for the semantic router."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _obvious_mixed_transaction_executors,
    _obvious_transfer_task_count,
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
    _semantic_schedule_read_request,
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
from shared.types.balance import BalanceQueryContract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    BeneficiaryQueryContract,
    ScheduleQueryContract,
)
from shared.types.planner import RouterDomainIntent, SemanticRoutingMode
from shared.types.query_preferences import QueryPreferenceUpdate
from shared.types.read import ReadRequest
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
        read_request = _semantic_schedule_read_request(route)
        if read_request is not None:
            logger.info(
                "gate_semantic_router_schedule_direct",
                decision=canonical_decision,
                mode=canonical_mode,
                response_shape=read_request.response_shape,
            )
            return _build_direct_schedule_read_updates(
                ctx,
                updates=updates,
                canonical_decision=canonical_decision,
                canonical_mode=canonical_mode,
                source="semantic_router",
                path_shape="semantic_router_domain",
                read_request=read_request,
                schedule_contract=(
                    route.schedule_contract
                    if isinstance(getattr(route, "schedule_contract", None), ScheduleQueryContract)
                    else None
                ),
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
        updates["preplanner_expected_transaction_task_count"] = len(mixed_executors)
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

    # A same-executor transfer batch (for example, two independent sends in
    # one turn) has no mixed executor list, but it still needs planner
    # decomposition.  If semantic routing reaches this stage—typically
    # because a live interrupt or a locale-specific gate prevented the
    # deterministic transfer guard from running—preserve the typed count
    # contract instead of dispatching one direct transfer task.  The planner
    # materializer will fail closed if it cannot produce that many tasks.
    if domain == "transfer":
        expected_transfer_tasks = _obvious_transfer_task_count(ctx.message_text)
        # Do not apply the amount-count signal to captioned media/receipt
        # text.  Those turns can contain several numeric values but are still
        # one typed transfer instruction; only the classifier's explicit
        # batch-transfer decision authorizes decomposition.
        transfer_request_reason = _classify_obvious_transfer_request(ctx.message_text)
        if expected_transfer_tasks > 1 and transfer_request_reason == "batch_transfer_command":
            updates["preplanner_expected_transaction_executors"] = ["transfer"]
            updates["preplanner_expected_transaction_task_count"] = expected_transfer_tasks
            logger.info(
                "gate_semantic_router_same_executor_transfer_batch_veto",
                expected_task_count=expected_transfer_tasks,
            )
            return planner_handoff(
                ctx,
                owner="semantic_router",
                decision="planner_handoff",
                target_domain="transfer",
                mode=canonical_mode,
                source="semantic_router_same_executor_batch_veto",
                path_shape="semantic_router_same_executor_batch_planner_handoff",
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

    read_request = getattr(route, "read_request", None)
    if not isinstance(read_request, ReadRequest):
        read_request = None
    if domain in {"account", "beneficiary"} and read_request is None:
        logger.info(
            "gate_semantic_router_read_contract_missing",
            decision=canonical_decision,
            domain=domain,
            mode=canonical_mode,
        )
        return planner_handoff(
            ctx,
            owner="semantic_router",
            decision="planner_handoff",
            target_domain=domain,
            mode=canonical_mode,
            source="semantic_router",
            path_shape="semantic_router_missing_read_contract",
            extra_updates=updates,
        )
    balance_contract = getattr(route, "balance_contract", None)
    if not isinstance(balance_contract, BalanceQueryContract):
        balance_contract = None
    beneficiary_contract = getattr(route, "beneficiary_contract", None)
    if not isinstance(beneficiary_contract, BeneficiaryQueryContract):
        beneficiary_contract = None
    schedule_contract = getattr(route, "schedule_contract", None)
    if not isinstance(schedule_contract, ScheduleQueryContract):
        schedule_contract = None
    account_lifecycle_contract = getattr(route, "account_lifecycle_contract", None)
    if not isinstance(account_lifecycle_contract, AccountLifecycleContract):
        account_lifecycle_contract = None
    query_preferences = getattr(route, "query_preferences", None)
    if not isinstance(query_preferences, QueryPreferenceUpdate):
        query_preferences = None
    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain=domain,
        mode=canonical_mode,
        read_request=read_request,
        balance_contract=balance_contract,
        beneficiary_contract=beneficiary_contract,
        schedule_contract=schedule_contract,
        account_lifecycle_contract=account_lifecycle_contract,
        query_insight_type=getattr(route, "query_insight_type", None) if domain == "query" else None,
        query_preferences=query_preferences if domain == "query" else None,
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
