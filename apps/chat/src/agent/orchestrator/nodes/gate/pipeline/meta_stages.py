from typing import Any

from apps.chat.src.agent.orchestrator.banking_ambiguity import (
    classify_banking_coded_ambiguity,
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.nodes.cancellation import clear_query_session
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_context_recap_response,
    _build_direct_domain_task,
    _build_query_session_exit_updates,
    _classify_obvious_transfer_request,
    _is_direct_context_recap_request,
    _is_query_domain_request,
    _is_structural_query_domain_request,
    _locale_update,
    _query_followup_bypass_reason,
    _route_observability_updates,
    classify_deterministic_meta_response,
)
from shared.i18n.renderer import render_message
from shared.services.conversation_responder import is_contextual_casual_followup_turn
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_banking_ambiguity(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic ambiguous banking clarification."""
    ctx.ambiguous_banking_domain = classify_banking_coded_ambiguity(ctx.message_text)
    if (
        ctx.ambiguous_banking_domain is not None
        and not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and not ctx.state.session_stack
        and not ctx.state.waves
        and ctx.state.pending_interrupt is None
    ):
        logger.info(
            "gate_banking_coded_ambiguity_clarify",
            domain=ctx.ambiguous_banking_domain,
        )
        return {
            **ctx.gate_updates,
            "direct_path_triggered": True,
            "final_response": render_banking_coded_ambiguity_prompt(ctx.message_text, locale=ctx.current_locale),
            "semantic_path_shape": "banking_coded_ambiguity_clarify",
            **_route_observability_updates(
                owner="guardrail",
                decision=f"banking_coded_ambiguity_{ctx.ambiguous_banking_domain}",
            ),
        }
    return None


async def _stage_deterministic_meta(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic meta response (greeting, appreciation, identity, etc.)."""
    if ctx.live_pending_interrupt or ctx.state.has_quote:
        return None
    deterministic_meta = classify_deterministic_meta_response(ctx.message_text)
    if not deterministic_meta:
        return None
    response_key, response_locale = deterministic_meta
    locale = response_locale or ctx.current_locale
    locale_updates = _locale_update(ctx.state, locale) if response_locale else {}
    await ctx.ensure_query_session()
    exit_updates = _build_query_session_exit_updates(
        ctx.state,
        query_session_snapshot=ctx.query_session_snapshot,
    )
    if (
        ctx.redis_client
        and isinstance(ctx.query_session_snapshot, dict)
        and ctx.query_session_snapshot.get("session_active")
    ):
        await clear_query_session(ctx.redis_client, ctx.state.phone_number)
    if exit_updates:
        logger.info("gate_query_session_exited_on_direct_reply", had_pending_clarification=False)
    return {
        **ctx.gate_updates,
        **exit_updates,
        **locale_updates,
        "direct_path_triggered": True,
        "final_response": render_message(response_key, locale),
        "semantic_path_shape": "meta_direct",
        **_route_observability_updates(owner="guardrail", decision="meta_direct"),
    }


async def _stage_deterministic_domains(ctx: GateContext) -> dict[str, Any] | None:
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

    # Contextual casual followup
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

    # Context recap
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

    # Query followup bypass
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

    # Deterministic query domain
    if (
        not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and not has_active_query_session
        and ctx.phrase_heavy_fastpath_allowed
        and _is_query_domain_request(ctx.message_text)
    ):
        semantic_router_available = callable(getattr(ctx.task_planner, "route_semantic_turn", None))
        structural_query_request = _is_structural_query_domain_request(ctx.message_text)
        if semantic_router_available and not structural_query_request:
            ctx.add_routing_hint(
                domain="query",
                reason="query_domain_phrase",
                source="query_domain_phrase",
            )
            logger.info("gate_query_domain_hint_attached")
            return None

        task_id, spec = _build_direct_domain_task(state=ctx.state, domain="query", mode="new")
        heuristic_type = "guardrail_shortcut" if structural_query_request else "routing_heuristic"
        heuristic_name = "structural_query_domain" if structural_query_request else "query_domain_phrase"
        logger.info(
            "gate_deterministic_query_domain",
            task_id=task_id,
            structural_query_request=structural_query_request,
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
                heuristic_type=heuristic_type,
                heuristic_name=heuristic_name,
            ),
        }

    # Transfer direct
    if not ctx.live_pending_interrupt and not ctx.state.has_quote:
        transfer_request_reason = (
            _classify_obvious_transfer_request(ctx.message_text) if ctx.phrase_heavy_fastpath_allowed else None
        )
        if transfer_request_reason in {"fresh_transfer_command", "fresh_transfer_missing_recipient_command"}:
            transfer_updates: dict[str, Any] = {}
            if (
                isinstance(ctx.query_session_snapshot, dict)
                and ctx.query_session_snapshot.get("session_active")
            ):
                await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                transfer_updates.update(
                    _build_query_session_exit_updates(
                        ctx.state,
                        query_session_snapshot=ctx.query_session_snapshot,
                    )
                )
            task_id, spec = _build_direct_domain_task(state=ctx.state, domain="transfer", mode="new")
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
            if (
                isinstance(ctx.query_session_snapshot, dict)
                and ctx.query_session_snapshot.get("session_active")
            ):
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
