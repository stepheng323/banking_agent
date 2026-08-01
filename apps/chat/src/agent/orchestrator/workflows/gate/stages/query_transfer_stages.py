from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import ConversationResponseMode
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import is_contextual_casual_followup_turn
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_query_domain_request,
    _is_structural_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.query_followups import (
    _query_followup_bypass_reason,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import (
    direct_response,
    planner_handoff,
    task_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.gate.utils.router_context import (
    _build_direct_context_recap_response,
    _is_direct_context_recap_request,
)
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def _has_query_session_stack(ctx: GateContext) -> bool:
    session = ctx.state_view.active_session
    return bool(session and session.domain == "query")


def _can_consider_query_domain(ctx: GateContext, *, has_active_query_session: bool) -> bool:
    return (
        not ctx.live_pending_interrupt
        and not ctx.state_view.has_quote
        and not has_active_query_session
        and ctx.phrase_heavy_fastpath_allowed
    )


def _can_consider_structural_query_domain(ctx: GateContext) -> bool:
    return not ctx.live_pending_interrupt and not ctx.state_view.has_quote and ctx.phrase_heavy_fastpath_allowed


def _can_consider_contextual_casual_followup(ctx: GateContext, *, has_active_query_session: bool) -> bool:
    return (
        not ctx.live_pending_interrupt
        and not ctx.state_view.has_quote
        and not has_active_query_session
        and not ctx.state_view.has_gate_blocking_state
        and is_contextual_casual_followup_turn(
            ctx.message_text,
            ctx.state_view.loaded_context_or_empty.get("history"),
        )
    )


async def _maybe_contextual_casual_followup(
    ctx: GateContext,
    *,
    has_active_query_session: bool,
) -> RouteResolution | None:
    if _can_consider_contextual_casual_followup(ctx, has_active_query_session=has_active_query_session):
        responder_reply = await _build_bounded_conversational_reply(
            ctx,
            ctx.current_locale,
            mode=ConversationResponseMode.CONTEXTUAL_WORKER,
        )
        if responder_reply:
            logger.info("gate_contextual_casual_followup_responder")
            return direct_response(
                ctx,
                response=responder_reply,
                owner="guardrail",
                decision="contextual_casual_followup",
                path_shape="contextual_casual_followup",
                extra_updates=ctx.summary_updates,
            )
    return None


def _maybe_direct_context_recap(ctx: GateContext) -> RouteResolution | None:
    if ctx.turn_summary is None:
        return None
    if (
        not ctx.live_pending_interrupt
        and not ctx.state_view.has_quote
        and _is_direct_context_recap_request(ctx.message_text)
    ):
        response = _build_direct_context_recap_response(ctx.turn_summary)
        if response is not None:
            logger.info(
                "gate_direct_context_recap",
                focus=ctx.turn_summary.recent_answer_focus,
                active_flow=ctx.turn_summary.active_flow_intent,
            )
            return direct_response(
                ctx,
                response=response,
                owner="guardrail",
                decision="direct_context_recap",
                path_shape="direct_context_recap",
                extra_updates=ctx.summary_updates,
            )
        logger.info("gate_direct_context_recap_miss", reason="no_active_context")
    return None


async def _maybe_query_followup_bypass(ctx: GateContext) -> RouteResolution | None:
    if not ctx.live_pending_interrupt and not ctx.state_view.has_quote:
        raw_pending = ctx.state_view.pending_query_clarification
        has_pending_query_input = isinstance(raw_pending, dict) and bool(
            raw_pending.get("pending_clarification") or raw_pending.get("pending_input")
        )
        active_query = await ctx.has_active_query_session()
        bypass_reason, bypass_detail = _query_followup_bypass_reason(
            message_text=ctx.message_text,
            locale=ctx.current_locale,
            # A persisted pending-input contract is itself an active query,
            # even if the session stack/context frame has not been rebuilt yet.
            has_active_query_session=active_query or has_pending_query_input,
            has_recent_query_context=ctx.state_view.recent_query_context is not None,
            is_pending_clarification=bool(
                has_pending_query_input
                or (
                    isinstance(ctx.query_session_snapshot, dict)
                    and ctx.query_session_snapshot.get("pending_clarification")
                )
            ),
        )
        if bypass_reason is not None:
            logger.info(
                "query_followup_bypass_hit",
                reason=bypass_reason,
                detail=bypass_detail,
                query_session_source=ctx.query_session_source,
            )
            mode = "new" if bypass_reason == "replacement_query" else "continuation"
            task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="query", mode=mode)
            return task_dispatch(
                ctx,
                tasks={task_id: spec},
                waves=[[task_id]],
                owner="query_session",
                decision="query_followup_bypass",
                path_shape="query_followup_bypass",
                extra_updates=ctx.summary_updates,
                target_domain="query",
                mode=mode,
            )
    return None


def _maybe_structural_query_domain(ctx: GateContext, *, can_consider_query_domain: bool) -> RouteResolution | None:
    if not can_consider_query_domain or not _is_structural_query_domain_request(ctx.message_text):
        return None
    semantic_router_available = ctx.task_planner is not None
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="query", mode="new")
    logger.info(
        "gate_deterministic_query_domain",
        task_id=task_id,
        structural_query_request=True,
        semantic_router_available=semantic_router_available,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="deterministic_query_domain",
        path_shape="deterministic_query_domain",
        extra_updates=ctx.summary_updates,
        target_domain="query",
        mode="new",
        source="query_domain_guard",
        heuristic_type="guardrail_shortcut",
        heuristic_name="structural_query_domain",
    )


def _attach_query_domain_hint_if_needed(ctx: GateContext, *, can_consider_query_domain: bool) -> bool:
    if can_consider_query_domain and ctx.task_planner is not None and _is_query_domain_request(ctx.message_text):
        ctx.add_routing_hint(
            domain="query",
            reason="query_domain_phrase",
            source="query_domain_phrase",
        )
        logger.info("gate_query_domain_hint_attached")
        return True
    return False


async def _query_session_exit_updates_if_needed(ctx: GateContext) -> dict[str, Any]:
    if not await ctx.has_active_query_session():
        return {}
    return _build_query_session_exit_updates(
        ctx.state,
    )


async def _maybe_transfer_route(ctx: GateContext) -> RouteResolution | None:
    if not ctx.live_pending_interrupt and not ctx.state_view.has_quote:
        transfer_request_reason = (
            _classify_obvious_transfer_request(ctx.message_text) if ctx.phrase_heavy_fastpath_allowed else None
        )
        if transfer_request_reason in {
            "fresh_transfer_command",
            "fresh_transfer_missing_recipient_command",
            "recipient_bank_details_only",
        }:
            transfer_updates = await _query_session_exit_updates_if_needed(ctx)
            task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="transfer", mode="new")
            if transfer_request_reason == "recipient_bank_details_only":
                spec.payload["amount_suggestion_disabled"] = True
            logger.info(
                "gate_deterministic_transfer_domain",
                task_id=task_id,
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                skipped_planner=True,
            )
            return task_dispatch(
                ctx,
                tasks={task_id: spec},
                waves=[[task_id]],
                owner="guardrail",
                decision=transfer_request_reason,
                path_shape="deterministic_transfer_domain",
                extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
                target_domain="transfer",
                mode="new",
                source="transfer_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name=transfer_request_reason,
            )
        if transfer_request_reason in {"batch_transfer_command", "account_aware_transfer_command"}:
            transfer_updates = {
                "preplanner_expected_transaction_executors": ["transfer"],
            }
            transfer_updates.update(await _query_session_exit_updates_if_needed(ctx))
            logger.info(
                "gate_transfer_planner_handoff",
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                target_domain="transfer",
            )
            return planner_handoff(
                ctx,
                owner="guardrail",
                decision=transfer_request_reason,
                extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
                target_domain="transfer",
                mode="new",
                source="transfer_domain_guard",
                path_shape="transfer_planner_handoff",
                heuristic_type="slot_parser",
                heuristic_name=transfer_request_reason,
            )

    return None


async def _maybe_mixed_transaction_planner_handoff(ctx: GateContext) -> RouteResolution | None:
    if ctx.live_pending_interrupt or ctx.state_view.has_quote or not ctx.phrase_heavy_fastpath_allowed:
        return None

    expected_executors = _obvious_mixed_transaction_executors(ctx.message_text)
    if not expected_executors:
        return None

    transfer_updates = {
        "preplanner_expected_transaction_executors": expected_executors,
    }
    transfer_updates.update(await _query_session_exit_updates_if_needed(ctx))
    logger.info(
        "gate_mixed_transaction_planner_handoff",
        expected_executors=expected_executors,
        skipped_semantic_router=True,
    )
    return planner_handoff(
        ctx,
        owner="guardrail",
        decision="planner_mixed",
        extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
        mode="new",
        source="mixed_transaction_guard",
        path_shape="mixed_transaction_planner_handoff",
        heuristic_type="slot_parser",
        heuristic_name="mixed_transaction_command",
    )


async def _stage_query_and_transfer_domain_guards(ctx: GateContext) -> RouteResolution | None:
    """Context recap, casual followup, query followup, query domain, and transfer direct."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    has_active_query_session = await ctx.has_active_query_session()
    has_query_session_stack = _has_query_session_stack(ctx)
    log_orchestrator_diagnostic(
        logger,
        "gate_query_routing_breadcrumb",
        path="query_session_context",
        has_active_query_session=has_active_query_session or has_query_session_stack,
        query_session_source=ctx.query_session_source,
        query_session_stack=has_query_session_stack,
    )

    if updates := await _maybe_contextual_casual_followup(ctx, has_active_query_session=has_active_query_session):
        return updates
    if updates := _maybe_direct_context_recap(ctx):
        return updates
    if updates := _maybe_structural_query_domain(
        ctx,
        can_consider_query_domain=_can_consider_structural_query_domain(ctx),
    ):
        return updates
    # A new executable command takes precedence over the read-only query
    # continuation.  These guards are deterministic and preserve the normal
    # transfer/mixed-operation ownership without spending a router call.
    if updates := await _maybe_mixed_transaction_planner_handoff(ctx):
        return updates
    if updates := await _maybe_transfer_route(ctx):
        return updates
    # An active query owns its read-only follow-ups.  Dispatch it before the
    # broad semantic router so the query reasoner/parser is the one semantic
    # interpretation call for this turn.  The bypass classifier explicitly
    # yields to new transfer, airtime, and data commands.
    if updates := await _maybe_query_followup_bypass(ctx):
        return updates
    if has_active_query_session and ctx.task_planner is not None:
        ctx.add_routing_hint(
            domain="query",
            reason="active_query_session",
            source="active_query_session",
        )
        logger.info(
            "gate_active_query_session_deferred_to_semantic_router",
            query_session_source=ctx.query_session_source,
        )
        return None
    can_consider_query_domain = _can_consider_query_domain(ctx, has_active_query_session=has_active_query_session)
    if _attach_query_domain_hint_if_needed(ctx, can_consider_query_domain=can_consider_query_domain):
        return None

    return None
