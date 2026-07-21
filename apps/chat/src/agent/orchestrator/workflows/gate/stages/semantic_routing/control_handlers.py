"""Control logic updates for semantic routing."""

from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, planner_handoff
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import (
    TRANSACTION_EXECUTORS,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import (
    _build_direct_schedule_read_updates,
    _semantic_schedule_read_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _locale_update
from apps.chat.src.agent.orchestrator.workflows.gate.utils.language import (
    _looks_like_language_switch_request,
)
from banking.presentation.i18n.bridge import render_locale_switched
from banking.presentation.i18n.locale import LocaleManager
from shared.types.conversation_sets import ScheduleQueryContract
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _has_support_issue_hint(ctx: GateContext) -> bool:
    return any(hint.get("domain") == "support" and hint.get("source") != "stale_context" for hint in ctx.routing_hints)


def support_hint_veto_updates(
    ctx: GateContext,
    *,
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution | None:
    if not _has_support_issue_hint(ctx) or canonical_decision not in {
        "domain_query",
        "direct_reply",
        "direct_context_answer",
        "planner_ambiguous",
    }:
        return None
    logger.info(
        "gate_semantic_router_support_hint_veto",
        decision=canonical_decision,
        mode=canonical_mode,
    )
    return planner_handoff(
        ctx,
        owner="semantic_router",
        decision="support_hint_planner_handoff",
        target_domain=None,
        mode=canonical_mode,
        source="semantic_router_veto",
        path_shape="support_hint_planner_handoff",
        heuristic_type="routing_hint",
        heuristic_name="support_issue_phrase",
    )


async def semantic_locale_switch_updates(
    ctx: GateContext,
    route: Any,
    *,
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution | None:
    requested_locale = getattr(route, "requested_language", None)
    if not requested_locale:
        return None
    if not _looks_like_language_switch_request(ctx.message_text, requested_locale):
        logger.info(
            "gate_semantic_router_locale_switch_ignored",
            requested_locale=requested_locale,
            reason="message_missing_language_switch_cues",
        )
        return None

    resolved_locale = LocaleManager.parse_locale_name(requested_locale)
    if resolved_locale is None:
        logger.info("gate_semantic_router_locale_switch_invalid", requested_locale=requested_locale)
        return None

    if ctx.redis_client:
        resolved = await LocaleManager.set_locale(
            ctx.state_view.phone_number,
            resolved_locale,
            source="user_command",
        )
        next_locale = resolved.value
    else:
        next_locale = resolved_locale.value
    logger.info("gate_semantic_router_locale_switch", locale=next_locale)
    return direct_response(
        ctx,
        response=render_locale_switched(next_locale),
        owner="semantic_router",
        decision=canonical_decision or "direct_reply",
        mode=canonical_mode,
        source="semantic_router",
        path_shape="semantic_locale_switch",
        extra_updates={
            **(ctx.summary_updates or {}),
            **_locale_update(ctx.state_view, next_locale),
        },
    )


def semantic_route_executor_updates(route: Any) -> tuple[dict[str, Any], list[str]]:
    expected_executors = [
        str(item)
        for item in (getattr(route, "expected_transaction_executors", None) or [])
        if str(item) in TRANSACTION_EXECUTORS
    ]
    if not expected_executors:
        return {}, []
    return {"preplanner_expected_transaction_executors": expected_executors}, expected_executors


async def semantic_cancel_updates(
    ctx: GateContext,
    route: Any,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution | None:
    if canonical_decision != "cancel":
        return None

    locale = ctx.current_locale
    if has_cancelable_state(ctx.state):
        text = cancelled_message(ctx.state, locale)
        updates.update(await build_cancellation_reset_updates(ctx.state, ctx.redis_client))
    else:
        text = clarify_message(ctx.state, locale)
    return direct_response(
        ctx,
        response=text,
        owner="semantic_router",
        decision=canonical_decision,
        mode=canonical_mode,
        source="semantic_router",
        path_shape="semantic_router_direct",
        extra_updates={**(ctx.summary_updates or {}), **updates},
    )


def semantic_schedule_target_updates(
    ctx: GateContext,
    route: Any,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution | None:
    if getattr(route, "target_intent", None) != "schedule":
        return None

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
            source="semantic_router_target_intent",
            path_shape="semantic_router_domain",
            read_request=read_request,
            schedule_contract=(
                route.schedule_contract
                if isinstance(getattr(route, "schedule_contract", None), ScheduleQueryContract)
                else None
            ),
        )

    logger.info(
        "gate_semantic_router_schedule_target_planner_handoff",
        decision=canonical_decision,
        mode=canonical_mode,
    )
    return planner_handoff(
        ctx,
        owner="semantic_router",
        decision="planner_handoff",
        target_domain="schedule",
        mode=canonical_mode,
        source="semantic_router_target_intent",
        path_shape="semantic_router_schedule_planner_handoff",
        extra_updates=updates,
    )


def semantic_executor_handoff_updates(
    ctx: GateContext,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution:
    return planner_handoff(
        ctx,
        owner="semantic_router",
        decision=canonical_decision or "planner_handoff",
        mode=canonical_mode,
        source="semantic_router",
        path_shape="semantic_executor_planner_handoff",
        extra_updates=updates,
    )
