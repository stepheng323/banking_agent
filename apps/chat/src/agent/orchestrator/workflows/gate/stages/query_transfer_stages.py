from typing import Any

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
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.gate.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.router_context import (
    _build_direct_context_recap_response,
    _is_direct_context_recap_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import is_contextual_casual_followup_turn
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_query_and_transfer_domain_guards(ctx: GateContext) -> dict[str, Any] | None:
    """Context recap, casual followup, query followup, query domain, and transfer direct."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    has_active_query_session = bool(
        isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active")
    )
    session = ctx.state.session_stack[-1] if ctx.state.session_stack else None
    has_query_session_stack = bool(session and session.domain == "query")
    logger.info(
        "gate_query_routing_breadcrumb",
        path="query_session_context",
        has_active_query_session=has_active_query_session or has_query_session_stack,
        query_session_source=ctx.query_session_source,
        query_session_stack=has_query_session_stack,
    )

    contextual_casual_followup = (
        not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and not has_active_query_session
        and not ctx.state.session_stack
        and not ctx.state.waves
        and ctx.state.pending_interrupt is None
        and is_contextual_casual_followup_turn(
            ctx.message_text,
            (ctx.state.loaded_context or {}).get("history") if isinstance(ctx.state.loaded_context, dict) else None,
        )
    )
    if contextual_casual_followup:
        responder_reply = await _build_bounded_conversational_reply(ctx, ctx.current_locale)
        if responder_reply:
            logger.info("gate_contextual_casual_followup_responder")
            return {
                **ctx.gate_updates,
                **(ctx.summary_updates or {}),
                "direct_path_triggered": True,
                "final_response": responder_reply,
                "semantic_path_shape": "contextual_casual_followup",
                **_route_observability_updates(
                    owner="guardrail",
                    decision="contextual_casual_followup",
                ),
            }

    if (
        not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and _is_direct_context_recap_request(ctx.message_text)
    ):
        response = _build_direct_context_recap_response(ctx.turn_summary)
        if response is not None:
            logger.info(
                "gate_direct_context_recap",
                focus=ctx.turn_summary.recent_answer_focus,
                active_flow=ctx.turn_summary.active_flow_intent,
            )
            return {
                **ctx.gate_updates,
                **(ctx.summary_updates or {}),
                "direct_path_triggered": True,
                "final_response": response,
                "semantic_path_shape": "direct_context_recap",
                **_route_observability_updates(owner="guardrail", decision="direct_context_recap"),
            }
        logger.info("gate_direct_context_recap_miss", reason="no_active_context")

    if not ctx.live_pending_interrupt and not ctx.state.has_quote:
        bypass_reason, bypass_detail = _query_followup_bypass_reason(
            message_text=ctx.message_text,
            locale=ctx.current_locale,
            query_session_snapshot=ctx.query_session_snapshot if isinstance(ctx.query_session_snapshot, dict) else None,
            has_context_frames=bool(ctx.state.context_frames),
        )
        if bypass_reason is not None:
            logger.info(
                "query_followup_bypass_hit",
                reason=bypass_reason,
                detail=bypass_detail,
                query_session_source=ctx.query_session_source,
            )
            task_id, spec = _build_direct_domain_task(state=ctx.state, domain="query")
            return {
                **ctx.gate_updates,
                **(ctx.summary_updates or {}),
                "tasks": {task_id: spec},
                "waves": [[task_id]],
                "current_wave_index": 0,
                "planner_output": None,
                "direct_path_triggered": True,
                "semantic_path_shape": "query_followup_bypass",
                **_route_observability_updates(
                    owner="query_session",
                    decision="query_followup_bypass",
                    target_domain="query",
                    mode="continuation",
                ),
            }

    can_consider_query_domain = (
        not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and not has_active_query_session
        and ctx.phrase_heavy_fastpath_allowed
    )
    if can_consider_query_domain and _is_structural_query_domain_request(ctx.message_text):
        semantic_router_available = ctx.task_planner is not None
        task_id, spec = _build_direct_domain_task(state=ctx.state, domain="query", mode="new")
        logger.info(
            "gate_deterministic_query_domain",
            task_id=task_id,
            structural_query_request=True,
            semantic_router_available=semantic_router_available,
        )
        return {
            **ctx.gate_updates,
            **(ctx.summary_updates or {}),
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_query_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_query_domain",
                target_domain="query",
                mode="new",
                route_source="query_domain_guard",
                heuristic_type="guardrail_shortcut",
                heuristic_name="structural_query_domain",
            ),
        }
    if can_consider_query_domain and ctx.task_planner is not None and _is_query_domain_request(ctx.message_text):
        ctx.add_routing_hint(
            domain="query",
            reason="query_domain_phrase",
            source="query_domain_phrase",
        )
        logger.info("gate_query_domain_hint_attached")
        return None

    if not ctx.live_pending_interrupt and not ctx.state.has_quote:
        transfer_request_reason = (
            _classify_obvious_transfer_request(ctx.message_text) if ctx.phrase_heavy_fastpath_allowed else None
        )
        if transfer_request_reason in {
            "fresh_transfer_command",
            "fresh_transfer_missing_recipient_command",
            "recipient_bank_details_only",
        }:
            transfer_updates: dict[str, Any] = {}
            if isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active"):
                await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                transfer_updates.update(
                    _build_query_session_exit_updates(
                        ctx.state,
                        query_session_snapshot=ctx.query_session_snapshot,
                    )
                )
            task_id, spec = _build_direct_domain_task(state=ctx.state, domain="transfer", mode="new")
            if transfer_request_reason == "recipient_bank_details_only":
                spec.payload["amount_suggestion_disabled"] = True
            logger.info(
                "gate_deterministic_transfer_domain",
                task_id=task_id,
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                skipped_planner=True,
            )
            return {
                **ctx.gate_updates,
                **(ctx.summary_updates or {}),
                **transfer_updates,
                "tasks": {task_id: spec},
                "waves": [[task_id]],
                "current_wave_index": 0,
                "planner_output": None,
                "direct_path_triggered": True,
                "semantic_path_shape": "deterministic_transfer_domain",
                **_route_observability_updates(
                    owner="guardrail",
                    decision=transfer_request_reason,
                    target_domain="transfer",
                    mode="new",
                    route_source="transfer_domain_guard",
                    heuristic_type="slot_parser",
                    heuristic_name=transfer_request_reason,
                ),
            }
        if transfer_request_reason in {"batch_transfer_command", "account_aware_transfer_command"}:
            transfer_updates = {
                "preplanner_expected_transaction_executors": ["transfer"],
            }
            if isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active"):
                await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                transfer_updates.update(
                    _build_query_session_exit_updates(
                        ctx.state,
                        query_session_snapshot=ctx.query_session_snapshot,
                    )
                )
            logger.info(
                "gate_transfer_planner_handoff",
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                target_domain="transfer",
            )
            return {
                **ctx.gate_updates,
                **(ctx.summary_updates or {}),
                **transfer_updates,
                **_route_observability_updates(
                    owner="guardrail",
                    decision=transfer_request_reason,
                    target_domain="transfer",
                    mode="new",
                    route_source="transfer_domain_guard",
                    heuristic_type="slot_parser",
                    heuristic_name=transfer_request_reason,
                ),
            }

    return None
