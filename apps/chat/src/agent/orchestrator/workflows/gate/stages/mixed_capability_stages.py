from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.mixed_capabilities import (
    MixedCapabilityMatch,
    SupportedClause,
    analyze_mixed_supported_unsupported,
    analyze_mixed_supported_unsupported_semantic,
    mixed_clarify_params,
    mixed_policy_notice,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, policy_block, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from banking.presentation.i18n.renderer import render_message
from shared.types.balance import BalanceQueryContract
from shared.types.conversation_sets import ScheduleQueryContract
from shared.types.read import ReadRequest
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_supported_task(state_view: GateStateView, supported: SupportedClause) -> tuple[str, TaskSpec]:
    if supported.domain == "account" and supported.heuristic_name == "balance_request":
        return _build_direct_domain_task(
            state_view=state_view,
            domain="account",
            mode="new",
            message_text=supported.text,
            read_request=ReadRequest(subject="balance", response_shape="fact_value"),
            balance_contract=BalanceQueryContract(),
        )
    if supported.domain == "schedule":
        return _build_direct_domain_task(
            state_view=state_view,
            domain="schedule",
            mode="new",
            message_text=supported.text,
            read_request=ReadRequest(subject="schedule", response_shape="surface_list"),
            schedule_contract=ScheduleQueryContract(),
        )
    return _build_direct_domain_task(
        state_view=state_view,
        domain=supported.domain,
        mode="new",
        message_text=supported.text,
    )


def _mixed_capability_eligible(ctx: GateContext) -> bool:
    return (
        not ctx.live_pending_interrupt
        and not ctx.state_view.has_gate_blocking_state
        and ctx.phrase_heavy_fastpath_allowed
    )


async def _classify_mixed_capability(ctx: GateContext) -> MixedCapabilityMatch | None:
    match = analyze_mixed_supported_unsupported(ctx.message_text)
    if match is not None:
        return match
    return await analyze_mixed_supported_unsupported_semantic(
        text=ctx.message_text,
        locale=ctx.current_locale,
        capability_classifier_llm=ctx.capability_classifier_llm,
    )


def _mixed_clarify_updates(ctx: GateContext, match: MixedCapabilityMatch) -> RouteResolution:
    logger.info(
        "gate_mixed_capability_ambiguous",
        supported_count=len(match.supported),
        unsupported=[item.key for item in match.unsupported],
    )
    return direct_response(
        ctx,
        response=render_message(
            "orchestrator.ambiguity.mixed_supported_unsupported",
            ctx.current_locale,
            mixed_clarify_params(match, locale=ctx.current_locale),
        ),
        owner="guardrail",
        decision="mixed_supported_unsupported_clarify",
        path_shape="mixed_capability_clarify",
        extra_updates={"capability_boundary": None},
    )


def _mixed_policy_block_updates(
    ctx: GateContext,
    *,
    supported: SupportedClause,
    notice: str,
    block_message: str,
) -> RouteResolution:
    return policy_block(
        ctx,
        response=f"{notice}\n\n{block_message}",
        decision="mixed_supported_unsupported_policy_blocked",
        path_shape="mixed_capability_supported_policy_blocked",
        extra_updates={"capability_boundary": None},
        target_domain=supported.domain,
    )


async def _mixed_supported_task_extra_updates(ctx: GateContext, supported: SupportedClause) -> dict[str, Any]:
    return {}


async def _mixed_supported_direct_updates(
    ctx: GateContext,
    *,
    match: MixedCapabilityMatch,
    supported: SupportedClause,
    notice: str,
) -> RouteResolution:
    task_id, spec = _build_supported_task(ctx.state_view, supported)
    task_updates = await _mixed_supported_task_extra_updates(ctx, supported)
    if supported.domain == "transfer" and supported.heuristic_name == "recipient_bank_details_only":
        spec.payload["amount_suggestion_disabled"] = True

    logger.info(
        "gate_mixed_capability_supported_direct",
        supported_domain=supported.domain,
        unsupported=[item.key for item in match.unsupported],
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="mixed_supported_unsupported",
        path_shape="mixed_capability_supported_direct",
        extra_updates={
            **(ctx.summary_updates or {}),
            **task_updates,
            "capability_boundary": None,
            "policy_notice": notice,
            "pending_interrupt": None,
        },
        target_domain=supported.domain,
        mode="new",
        source="mixed_capability_guard",
        heuristic_type="clause_splitter",
        heuristic_name=supported.heuristic_name,
    )


async def _stage_mixed_supported_unsupported_capability(ctx: GateContext) -> RouteResolution | None:
    """Route one supported banking clause while refusing unsupported clauses."""
    if not _mixed_capability_eligible(ctx):
        return None

    match = await _classify_mixed_capability(ctx)
    if match is None:
        return None

    notice = mixed_policy_notice(match, locale=ctx.current_locale)
    if match.is_ambiguous:
        return _mixed_clarify_updates(ctx, match)

    supported = match.supported[0]
    if block_message := _direct_domain_capability_block_message(ctx.state_view, supported.domain):
        return _mixed_policy_block_updates(
            ctx,
            supported=supported,
            notice=notice,
            block_message=block_message,
        )

    return await _mixed_supported_direct_updates(ctx, match=match, supported=supported, notice=notice)
