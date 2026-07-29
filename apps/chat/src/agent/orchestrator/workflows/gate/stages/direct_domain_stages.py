from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_generic_account_balance_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _is_obvious_airtime_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import policy_block, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from shared.types.balance import BalanceQueryContract
from shared.types.read import ReadRequest, ResponseShape
from shared.utils.bank_aliases import extract_known_bank_names
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_balance_direct(ctx: GateContext) -> RouteResolution | None:
    """Dispatch an explicit balance read from typed domain and bank signals."""
    bank_names = extract_known_bank_names(ctx.message_text)
    high_confidence_balance_read = _is_generic_account_balance_request(ctx.message_text) or (
        bool(bank_names) and _is_account_balance_request(ctx.message_text)
    )
    if ctx.state_view.has_quote or not high_confidence_balance_read:
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "account"):
        return policy_block(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            path_shape="deterministic_balance_policy_blocked",
            target_domain="account",
            mode="new",
            source="balance_domain_guard",
        )

    response_shape: ResponseShape = "fact_value" if len(bank_names) <= 1 else "surface_list"
    contract = BalanceQueryContract(
        account_scope="named" if bank_names else "all",
        bank_names=bank_names,
        operation="value" if len(bank_names) == 1 else "compare" if bank_names else "total",
        response_shape=response_shape,
    )
    request = ReadRequest(
        subject="balance",
        response_shape=cast(Any, response_shape),
        bank_name=bank_names[0] if len(bank_names) == 1 else None,
    )
    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain="account",
        mode="new",
        read_request=request,
        balance_contract=contract,
    )
    extra_updates = {
        "pending_interrupt": None,
        **(ctx.summary_updates or {}),
        **_build_query_session_exit_updates(ctx.state),
    }
    logger.info(
        "gate_deterministic_balance_domain",
        bank_count=len(bank_names),
        account_scope=contract.account_scope,
        operation=contract.operation,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="deterministic_balance_read",
        path_shape="deterministic_balance_read",
        extra_updates=extra_updates,
        target_domain="account",
        mode="new",
        source="balance_domain_guard",
        heuristic_type="typed_domain_signal",
        heuristic_name="explicit_balance_read",
    )


async def _stage_account_domain(ctx: GateContext) -> RouteResolution | None:
    """Defer free-form account reads to the canonical semantic read contract."""
    del ctx
    return None


async def _stage_beneficiary_domain(ctx: GateContext) -> RouteResolution | None:
    """Defer free-form beneficiary reads to the canonical semantic read contract."""
    del ctx
    return None


async def _stage_airtime_domain(ctx: GateContext) -> RouteResolution | None:
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
        return policy_block(
            ctx,
            response=block_message,
            owner="guardrail",
            decision="capability_blocked",
            path_shape="deterministic_airtime_domain_policy_blocked",
            target_domain="airtime",
            mode="new",
            source="airtime_domain_guard",
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
        path_shape="deterministic_airtime_domain",
        extra_updates={"pending_interrupt": None},
        target_domain="airtime",
        mode="new",
        source="airtime_domain_guard",
        heuristic_type="slot_parser",
        heuristic_name="obvious_airtime_request",
    )
