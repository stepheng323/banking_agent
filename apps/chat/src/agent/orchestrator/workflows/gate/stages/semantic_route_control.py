from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.language import _looks_like_language_switch_request
from apps.chat.src.agent.orchestrator.workflows.gate.locale_state import (
    _effective_response_locale,
    _locale_update,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    TRANSACTION_EXECUTORS,
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.schedule_read_stage import (
    _build_direct_schedule_read_updates,
    _semantic_schedule_response_mode,
)
from banking.presentation.i18n.bridge import render_locale_switched
from banking.presentation.i18n.locale import LocaleManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def append_routing_hints(route_context: str, hints: list[dict[str, str]]) -> str:
    if not hints:
        return route_context
    lines = [
        "",
        "Routing hints are non-authoritative guardrail hints. Use them only when they match the user intent.",
    ]
    for hint in hints:
        domain = hint.get("domain") or "unknown"
        reason = hint.get("reason") or "unknown"
        source = hint.get("source") or "unknown"
        lines.append(f"- candidate_domain={domain}; reason={reason}; source={source}")
    return f"{route_context}\n" + "\n".join(lines)


def support_hint_veto_updates(
    ctx: GateContext,
    *,
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> dict[str, Any] | None:
    if not ctx.has_routing_hint("support") or canonical_decision not in {
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
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        "semantic_path_shape": "support_hint_planner_handoff",
        **_route_observability_updates(
            owner="planner",
            decision="support_hint_planner_handoff",
            target_domain=None,
            mode=canonical_mode,
            route_source="semantic_router_veto",
            heuristic_type="routing_hint",
            heuristic_name="support_issue_phrase",
        ),
    }


async def semantic_locale_switch_updates(
    ctx: GateContext,
    route: Any,
    *,
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> dict[str, Any] | None:
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
    return {
        "direct_path_triggered": True,
        "final_response": render_locale_switched(next_locale),
        **_locale_update(ctx.state_view, next_locale),
        **_route_observability_updates(
            owner="semantic_router",
            decision=canonical_decision or "direct_reply",
            mode=canonical_mode,
        ),
    }


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
) -> dict[str, Any] | None:
    if canonical_decision != "cancel":
        return None

    locale, detected_locale_updates = await _effective_response_locale(
        state_view=ctx.state_view,
        redis_client=ctx.redis_client,
        detected_language=getattr(route, "detected_language", None),
    )
    updates.update(detected_locale_updates)
    if has_cancelable_state(ctx.state):
        text = cancelled_message(ctx.state, locale)
        updates.update(await build_cancellation_reset_updates(ctx.state, ctx.redis_client))
    else:
        text = clarify_message(ctx.state, locale)
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        "direct_path_triggered": True,
        "final_response": text,
        "semantic_path_shape": "semantic_router_direct",
        **_route_observability_updates(
            owner="semantic_router",
            decision=canonical_decision,
            mode=canonical_mode,
        ),
        **updates,
    }


def semantic_schedule_target_updates(
    ctx: GateContext,
    route: Any,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> dict[str, Any] | None:
    if getattr(route, "target_intent", None) != "schedule":
        return None

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
            route_source="semantic_router_target_intent",
        )

    logger.info(
        "gate_semantic_router_schedule_target_planner_handoff",
        decision=canonical_decision,
        mode=canonical_mode,
    )
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        "semantic_path_shape": "semantic_router_schedule_planner_handoff",
        **_route_observability_updates(
            owner="planner",
            decision="planner_handoff",
            target_domain="schedule",
            mode=canonical_mode,
            route_source="semantic_router_target_intent",
        ),
        **updates,
    }


def semantic_executor_handoff_updates(
    ctx: GateContext,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> dict[str, Any]:
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **_route_observability_updates(
            owner="planner",
            decision=canonical_decision or "planner_handoff",
            mode=canonical_mode,
        ),
        **updates,
    }
