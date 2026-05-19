from typing import Any

from apps.chat.src.agent.orchestrator.banking_ambiguity import render_banking_coded_ambiguity_prompt
from apps.chat.src.agent.orchestrator.conversational_style import format_out_of_scope_reply
from apps.chat.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    clear_query_session,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    TRANSACTION_EXECUTORS,
    _build_direct_domain_task,
    _build_query_session_exit_updates,
    _build_semantic_router_context,
    _direct_domain_capability_block_message,
    _effective_response_locale,
    _is_numeric_input_interrupt_selection,
    _locale_update,
    _looks_like_language_switch_request,
    _obvious_mixed_transaction_executors,
    _route_observability_updates,
    _semantic_route_decision,
    _semantic_route_mode,
    _should_invoke_semantic_router,
)
from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from shared.i18n.bridge import render_locale_switched
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.services.conversation_responder import is_banking_refusal_reply
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _append_routing_hints(route_context: str, hints: list[dict[str, str]]) -> str:
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


def _semantic_route_vetoed_by_support_hint(canonical_decision: str | None) -> bool:
    return canonical_decision in {
        "domain_query",
        "direct_reply",
        "direct_context_answer",
        "planner_ambiguous",
    }


async def _stage_semantic_router(ctx: GateContext) -> dict[str, Any] | None:
    """LLM semantic router dispatch."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    interrupt_kind = getattr(ctx.state.pending_interrupt, "kind", None)
    skip_semantic_router_for_interrupt = ctx.live_pending_interrupt and (
        interrupt_kind in {"confirmation", "auth"} or _is_numeric_input_interrupt_selection(ctx.state, ctx.message_text)
    )

    if skip_semantic_router_for_interrupt:
        logger.info(
            "gate_semantic_router_skipped_for_interrupt",
            kind=interrupt_kind,
            task_ids=getattr(ctx.state.pending_interrupt, "task_ids", None),
        )

    if (
        not skip_semantic_router_for_interrupt
        and not ctx.state.has_quote
        and callable(getattr(ctx.task_planner, "route_semantic_turn", None))
        and (ctx.live_pending_interrupt or _should_invoke_semantic_router(ctx.message_text))
    ):
        try:
            route_context = _build_semantic_router_context(
                ctx.turn_summary,
                ctx.state.preplanner_expected_transaction_executors,
                message_text=ctx.message_text,
            )
            route_context = _append_routing_hints(route_context, ctx.routing_hints)
            try:
                route = await ctx.task_planner.route_semantic_turn(
                    ctx.state.phone_number,
                    ctx.message_text,
                    context=route_context,
                    path_label="direct_path",
                )
            except TypeError:
                route = await ctx.task_planner.route_semantic_turn(
                    ctx.state.phone_number,
                    ctx.message_text,
                    context=route_context,
                )
        except Exception as exc:
            logger.warning("gate_semantic_router_failed", error=str(exc))
            route = None

        if route is not None:
            canonical_decision = _semantic_route_decision(route)
            canonical_mode = _semantic_route_mode(route)
            if ctx.has_routing_hint("support") and _semantic_route_vetoed_by_support_hint(canonical_decision):
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
            requested_locale = getattr(route, "requested_language", None)
            if requested_locale and _looks_like_language_switch_request(ctx.message_text, requested_locale):
                resolved_locale = LocaleManager.parse_locale_name(requested_locale)
                if resolved_locale is None:
                    logger.info("gate_semantic_router_locale_switch_invalid", requested_locale=requested_locale)
                else:
                    if ctx.redis_client:
                        resolved = await LocaleManager.set_locale(
                            ctx.state.phone_number,
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
                        **_locale_update(ctx.state, next_locale),
                        **_route_observability_updates(
                            owner="semantic_router",
                            decision=canonical_decision or "direct_reply",
                            mode=canonical_mode,
                        ),
                    }
            elif requested_locale:
                logger.info(
                    "gate_semantic_router_locale_switch_ignored",
                    requested_locale=requested_locale,
                    reason="message_missing_language_switch_cues",
                )

            expected_executors = [
                str(item)
                for item in (getattr(route, "expected_transaction_executors", None) or [])
                if str(item) in TRANSACTION_EXECUTORS
            ]
            updates: dict[str, Any] = {}
            if expected_executors:
                updates["preplanner_expected_transaction_executors"] = expected_executors

            if ctx.live_pending_interrupt:
                route = None
                canonical_decision = None
                canonical_mode = None

            if route is not None and canonical_decision == "cancel":
                locale, detected_locale_updates = await _effective_response_locale(
                    state=ctx.state,
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

            if route is not None and canonical_decision in {"direct_reply", "direct_context_answer"}:
                locale, detected_locale_updates = await _effective_response_locale(
                    state=ctx.state,
                    redis_client=ctx.redis_client,
                    detected_language=getattr(route, "detected_language", None),
                )
                updates.update(detected_locale_updates)
                had_active_query_session = bool(
                    isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active")
                )
                if (
                    had_active_query_session
                    and canonical_decision == "direct_reply"
                    and route.response_key != "planner.cancelled"
                ):
                    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="query")
                    logger.info(
                        "gate_active_query_session_owns_direct_reply",
                        semantic_decision=canonical_decision,
                        response_key=route.response_key,
                    )
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
                        **updates,
                    }
                if (
                    ctx.ambiguous_banking_domain is not None
                    and not ctx.state.session_stack
                    and not ctx.state.waves
                    and ctx.state.pending_interrupt is None
                    and route.response_key in {"conversational.casual_chat", "conversational.out_of_scope"}
                ):
                    logger.info(
                        "gate_semantic_router_banking_coded_ambiguity_override",
                        domain=ctx.ambiguous_banking_domain,
                        response_key=route.response_key,
                    )
                    return {
                        **ctx.gate_updates,
                        **(ctx.summary_updates or {}),
                        "direct_path_triggered": True,
                        "final_response": render_banking_coded_ambiguity_prompt(ctx.message_text, locale=locale),
                        "semantic_path_shape": "banking_coded_ambiguity_clarify",
                        **_route_observability_updates(
                            owner="guardrail",
                            decision=f"banking_coded_ambiguity_{ctx.ambiguous_banking_domain}",
                        ),
                        **updates,
                    }
                if route.response_key:
                    if route.response_key == "conversational.casual_chat":
                        text = await _build_bounded_conversational_reply(ctx, locale) or render_message(
                            "conversational.out_of_scope",
                            locale,
                        )
                    elif route.response_key == "conversational.out_of_scope":
                        responder_reply = None
                        if (
                            canonical_decision == "direct_reply"
                            and (
                                not route.response
                                or is_banking_refusal_reply(route.response, locale=locale)
                            )
                        ):
                            responder_reply = await _build_bounded_conversational_reply(ctx, locale)
                        text = responder_reply or format_out_of_scope_reply(locale, route.response)
                    elif route.response_key == "planner.cancelled":
                        if has_cancelable_state(ctx.state):
                            text = cancelled_message(ctx.state, locale)
                            updates.update(await build_cancellation_reset_updates(ctx.state, ctx.redis_client))
                        else:
                            text = clarify_message(ctx.state, locale)
                    else:
                        text = render_message(route.response_key, locale)
                else:
                    text = route.response or ""
                    if not text:
                        responder_reply = await _build_bounded_conversational_reply(ctx, locale)
                        if responder_reply:
                            text = responder_reply
                    if not text:
                        fallback_key = (
                            "conversational.out_of_scope"
                            if canonical_decision == "direct_reply"
                            else "conversational.clarify"
                        )
                        text = render_message(fallback_key, locale)

                had_pending_query_clarification = bool(
                    isinstance(ctx.query_session_snapshot, dict)
                    and ctx.query_session_snapshot.get("pending_clarification")
                )
                if had_active_query_session:
                    await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                    updates.update(
                        _build_query_session_exit_updates(
                            ctx.state,
                            query_session_snapshot=ctx.query_session_snapshot,
                        )
                    )
                    logger.info(
                        "gate_query_session_exited_on_direct_reply",
                        had_pending_clarification=had_pending_query_clarification,
                    )
                logger.info(
                    "gate_semantic_router_direct_response",
                    decision=canonical_decision,
                    response_key=route.response_key,
                    locale=locale,
                )
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

            route_to_domain: dict[
                str,
                str,
            ] = {
                "domain_query": "query",
                "domain_account": "account",
                "domain_support": "support",
                "domain_beneficiary": "beneficiary",
                "domain_transfer": "transfer",
                "domain_airtime": "airtime",
                "domain_data": "data",
            }
            if route is not None and canonical_decision in route_to_domain:
                domain = route_to_domain[canonical_decision]
                if domain == "support" and looks_like_transaction_replay_modifier_request(ctx.message_text):
                    logger.info(
                        "gate_semantic_router_support_replay_modifier_veto",
                        decision=canonical_decision,
                        mode=canonical_mode,
                    )
                    if block_message := _direct_domain_capability_block_message(ctx.state, "transfer"):
                        logger.info(
                            "gate_semantic_router_replay_modifier_transfer_policy_blocked",
                            decision=canonical_decision,
                            mode=canonical_mode,
                        )
                        return {
                            **ctx.gate_updates,
                            **(ctx.summary_updates or {}),
                            "direct_path_triggered": True,
                            "final_response": block_message,
                            "semantic_path_shape": "transaction_replay_modifier_transfer_policy_blocked",
                            **_route_observability_updates(
                                owner="guardrail",
                                decision="capability_blocked",
                                target_domain="transfer",
                                mode=canonical_mode,
                                route_source="semantic_router_veto",
                                heuristic_type="negative_guard",
                                heuristic_name="transaction_replay_modifier",
                            ),
                            **updates,
                        }
                    if (
                        isinstance(ctx.query_session_snapshot, dict)
                        and ctx.query_session_snapshot.get("session_active")
                    ):
                        await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                        updates.update(
                            _build_query_session_exit_updates(
                                ctx.state,
                                query_session_snapshot=ctx.query_session_snapshot,
                            )
                        )
                    task_id, spec = _build_direct_domain_task(
                        state=ctx.state,
                        domain="transfer",
                        mode=canonical_mode,
                    )
                    return {
                        **ctx.gate_updates,
                        **(ctx.summary_updates or {}),
                        "tasks": {task_id: spec},
                        "waves": [[task_id]],
                        "current_wave_index": 0,
                        "planner_output": None,
                        "direct_path_triggered": True,
                        "semantic_path_shape": "transaction_replay_modifier_transfer",
                        **_route_observability_updates(
                            owner="guardrail",
                            decision="transaction_replay_modifier_transfer",
                            target_domain="transfer",
                            mode=canonical_mode,
                            route_source="semantic_router_veto",
                            heuristic_type="negative_guard",
                            heuristic_name="transaction_replay_modifier",
                        ),
                        **updates,
                    }
                mixed_executors = _obvious_mixed_transaction_executors(ctx.message_text)
                if domain in TRANSACTION_EXECUTORS and mixed_executors:
                    updates["preplanner_expected_transaction_executors"] = mixed_executors
                    logger.info(
                        "gate_semantic_router_mixed_veto",
                        decision=canonical_decision,
                        attempted_domain=domain,
                        expected_executors=mixed_executors,
                    )
                    return {
                        **ctx.gate_updates,
                        **(ctx.summary_updates or {}),
                        **_route_observability_updates(
                            owner="planner",
                            decision="planner_handoff",
                            mode=canonical_mode,
                        ),
                        **updates,
                    }
                if block_message := _direct_domain_capability_block_message(ctx.state, domain):
                    logger.info(
                        "gate_semantic_router_domain_policy_blocked",
                        decision=canonical_decision,
                        domain=domain,
                        mode=canonical_mode,
                    )
                    return {
                        **ctx.gate_updates,
                        **(ctx.summary_updates or {}),
                        "direct_path_triggered": True,
                        "final_response": block_message,
                        "semantic_path_shape": "semantic_router_domain_policy_blocked",
                        **_route_observability_updates(
                            owner="semantic_router",
                            decision="capability_blocked",
                            target_domain=domain,
                            mode=canonical_mode,
                        ),
                        **updates,
                    }
                if (
                    domain != "query"
                    and isinstance(ctx.query_session_snapshot, dict)
                    and ctx.query_session_snapshot.get("session_active")
                ):
                    await clear_query_session(ctx.redis_client, ctx.state.phone_number)
                    updates.update(
                        _build_query_session_exit_updates(
                            ctx.state,
                            query_session_snapshot=ctx.query_session_snapshot,
                        )
                    )
                task_id, spec = _build_direct_domain_task(
                    state=ctx.state,
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
                return {
                    **ctx.gate_updates,
                    **(ctx.summary_updates or {}),
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "direct_path_triggered": True,
                    "semantic_path_shape": "semantic_router_domain",
                    **_route_observability_updates(
                        owner="semantic_router",
                        decision=canonical_decision,
                        target_domain=domain,
                        mode=canonical_mode,
                    ),
                    **updates,
                }

            if updates:
                logger.info("gate_semantic_router_expected_executors", executors=expected_executors)
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

    return None
