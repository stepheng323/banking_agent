from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import is_contextual_casual_followup_turn
from apps.chat.src.agent.orchestrator.guardrails.cancellation import clear_query_session
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
    parse_source_aware_transfer_direct,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, hint_only, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.gate.utils.router_context import (
    _build_direct_context_recap_response,
    _is_direct_context_recap_request,
)
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def _has_active_query_session(ctx: GateContext) -> bool:
    return bool(
        (isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active"))
        or ctx.state_view.has_session_for_domain("query")
        or ctx.state_view.active_domain == "query"
    )


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
) -> dict[str, Any] | None:
    if _can_consider_contextual_casual_followup(ctx, has_active_query_session=has_active_query_session):
        responder_reply = await _build_bounded_conversational_reply(ctx, ctx.current_locale)
        if responder_reply:
            logger.info("gate_contextual_casual_followup_responder")
            return direct_response(
                ctx,
                response=responder_reply,
                owner="guardrail",
                decision="contextual_casual_followup",
                semantic_path_shape="contextual_casual_followup",
                extra_updates=ctx.summary_updates,
            )
    return None


def _maybe_direct_context_recap(ctx: GateContext) -> dict[str, Any] | None:
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
                semantic_path_shape="direct_context_recap",
                extra_updates=ctx.summary_updates,
            )
        logger.info("gate_direct_context_recap_miss", reason="no_active_context")
    return None


def _maybe_query_followup_bypass(ctx: GateContext) -> dict[str, Any] | None:
    if not ctx.live_pending_interrupt and not ctx.state_view.has_quote:
        bypass_reason, bypass_detail = _query_followup_bypass_reason(
            message_text=ctx.message_text,
            locale=ctx.current_locale,
            query_session_snapshot=ctx.query_session_snapshot if isinstance(ctx.query_session_snapshot, dict) else None,
            has_context_frames=ctx.state_view.has_context_frames,
        )
        if bypass_reason is not None:
            logger.info(
                "query_followup_bypass_hit",
                reason=bypass_reason,
                detail=bypass_detail,
                query_session_source=ctx.query_session_source,
            )
            task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="query")
            return task_dispatch(
                ctx,
                tasks={task_id: spec},
                waves=[[task_id]],
                owner="query_session",
                decision="query_followup_bypass",
                semantic_path_shape="query_followup_bypass",
                extra_updates=ctx.summary_updates,
                target_domain="query",
                mode="continuation",
            )
    return None



def _maybe_structural_query_domain(ctx: GateContext, *, can_consider_query_domain: bool) -> dict[str, Any] | None:
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
        semantic_path_shape="deterministic_query_domain",
        extra_updates=ctx.summary_updates,
        target_domain="query",
        mode="new",
        route_source="query_domain_guard",
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


def _source_account_candidates(ctx: GateContext) -> list[dict[str, Any]]:
    loaded_context = ctx.state_view.loaded_context_or_empty
    accounts: list[dict[str, Any]] = []
    for key in ("transaction_accounts", "accounts", "all_accounts"):
        raw_accounts = loaded_context.get(key)
        if not isinstance(raw_accounts, list):
            continue
        accounts.extend(account for account in raw_accounts if isinstance(account, dict))
    return accounts


async def _maybe_source_aware_transfer_route(ctx: GateContext) -> dict[str, Any] | None:
    parsed = parse_source_aware_transfer_direct(
        ctx.message_text,
        accounts=_source_account_candidates(ctx),
    )
    if parsed is None:
        return None

    transfer_updates = await _query_session_exit_updates_if_needed(ctx)
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="transfer", mode="new")
    spec.payload.update(parsed.to_payload())
    logger.info(
        "gate_deterministic_source_aware_transfer",
        task_id=task_id,
        source_bank_name=parsed.source_bank_name,
        has_narration=bool(parsed.narration),
        skipped_semantic_router=True,
        skipped_planner=True,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="source_aware_transfer_command",
        semantic_path_shape="deterministic_transfer_domain",
        extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
        target_domain="transfer",
        mode="new",
        route_source="transfer_domain_guard",
        heuristic_type="slot_parser",
        heuristic_name="source_aware_transfer_command",
    )


async def _query_session_exit_updates_if_needed(ctx: GateContext) -> dict[str, Any]:
    if not await ctx.has_active_query_session():
        return {}
    await clear_query_session(ctx.redis_client, ctx.state_view.phone_number)
    return _build_query_session_exit_updates(
        ctx.state,
        query_session_snapshot=ctx.query_session_snapshot,
    )


async def _maybe_transfer_route(ctx: GateContext) -> dict[str, Any] | None:
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
                semantic_path_shape="deterministic_transfer_domain",
                extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
                target_domain="transfer",
                mode="new",
                route_source="transfer_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name=transfer_request_reason,
            )
        if transfer_request_reason in {"batch_transfer_command", "account_aware_transfer_command"}:
            if transfer_request_reason == "account_aware_transfer_command":
                source_aware_updates = await _maybe_source_aware_transfer_route(ctx)
                if source_aware_updates is not None:
                    return source_aware_updates

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
            return hint_only(
                ctx,
                owner="guardrail",
                decision=transfer_request_reason,
                extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
                target_domain="transfer",
                mode="new",
                route_source="transfer_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name=transfer_request_reason,
            )

    return None


async def _maybe_mixed_transaction_planner_handoff(ctx: GateContext) -> dict[str, Any] | None:
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
    return hint_only(
        ctx,
        owner="planner",
        decision="planner_mixed",
        extra_updates={**(ctx.summary_updates or {}), **transfer_updates},
        mode="new",
        route_source="mixed_transaction_guard",
        heuristic_type="slot_parser",
        heuristic_name="mixed_transaction_command",
    )


async def _stage_query_and_transfer_domain_guards(ctx: GateContext) -> dict[str, Any] | None:
    """Context recap, casual followup, query followup, query domain, and transfer direct."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    has_active_query_session = _has_active_query_session(ctx)
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
    if updates := _maybe_query_followup_bypass(ctx):
        return updates

    can_consider_query_domain = _can_consider_query_domain(ctx, has_active_query_session=has_active_query_session)
    if updates := _maybe_structural_query_domain(ctx, can_consider_query_domain=can_consider_query_domain):
        return updates
    if _attach_query_domain_hint_if_needed(ctx, can_consider_query_domain=can_consider_query_domain):
        return None

    if updates := await _maybe_mixed_transaction_planner_handoff(ctx):
        return updates

    return await _maybe_transfer_route(ctx)
