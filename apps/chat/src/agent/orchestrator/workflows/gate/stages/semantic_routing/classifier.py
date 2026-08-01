"""Classifier module for the semantic router."""

from dataclasses import replace
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capability,
    should_try_semantic_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.casual import (
    looks_like_obvious_casual_or_meta_turn,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.utils.router_context import (
    _build_semantic_router_context,
    _should_invoke_semantic_router,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouterLLM
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_prompt_compiler import (
    SemanticRouterPromptSignals,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    _could_be_schedule_interrupt_read_request,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state as build_context_frame_context,
)
from banking.intent.routing_signals import (
    looks_like_support_problem_statement,
)
from shared.observability.llm import LLMCallDeadlineExceeded
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


def _skip_semantic_router_for_interrupt(ctx: GateContext) -> bool:
    interrupt_kind = ctx.state_view.pending_interrupt_kind
    if not ctx.live_pending_interrupt:
        return False
    if interrupt_kind in {"confirmation", "auth"}:
        return True
    if ctx.state_view.is_numeric_input_interrupt_selection(ctx.message_text):
        return True
    if interrupt_kind == "input" and (
        _could_be_schedule_interrupt_read_request(ctx.message_text)
        or looks_like_support_problem_statement(ctx.message_text)
    ):
        return True
    interrupt = ctx.state_view.pending_interrupt
    if interrupt is None:
        return False
    return False


def _semantic_router_can_run(ctx: GateContext, *, skip_for_interrupt: bool) -> bool:
    return (
        not skip_for_interrupt
        and ctx.semantic_router_llm is not None
        and (ctx.live_pending_interrupt or _should_invoke_semantic_router(ctx.message_text))
    )


async def _classify_semantic_route(ctx: GateContext) -> Any | None:
    router = ctx.semantic_router_llm
    if ctx.turn_summary is None or router is None:
        return None
    try:
        route_context = _build_semantic_router_context(
            ctx.turn_summary,
            ctx.state_view.preplanner_expected_transaction_executors,
            message_text=ctx.message_text,
        )
        route_context = append_routing_hints(route_context, ctx.routing_hints)
        route_kwargs: dict[str, Any] = {"context": route_context, "path_label": "direct_path"}
        if isinstance(router, SemanticRouterLLM):
            frame = ContextFrameManager().latest_active_frame(ctx.state)
            signals = SemanticRouterPromptSignals.from_summary(ctx.turn_summary)
            unsupported_candidate = detect_unsupported_capability(
                ctx.message_text
            ) is None and should_try_semantic_unsupported_capability(ctx.message_text)
            if unsupported_candidate:
                signals = replace(signals, unsupported_capability_candidate=True, direct_reply_candidate=True)
            elif looks_like_obvious_casual_or_meta_turn(ctx.message_text):
                signals = replace(signals, direct_reply_candidate=True)
            if frame is not None:
                # This is a displayed, bounded surface summary.  It carries no
                # routing authority; the returned selector is still resolved
                # deterministically against the frame.
                frame_context = build_context_frame_context(ctx.state)
                route_context = f"{route_context}\n\nEligible displayed context:\n{frame_context}"
                route_kwargs["context"] = route_context
                frame_family = (
                    "balance" if isinstance(frame.metadata.get("balance_contract"), dict) else frame.frame_type.value
                )
                signals = replace(signals, context_frame=True, context_frame_type=frame_family)
            route_kwargs["prompt_signals"] = signals
        return await router.route_semantic_turn(
            ctx.state_view.phone_number,
            ctx.message_text,
            **route_kwargs,
        )
    except LLMCallDeadlineExceeded:
        raise
    except Exception as exc:
        logger.warning("gate_semantic_router_failed", error=str(exc))
        return None
