"""Classifier module for the semantic router."""

from typing import Any

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
            route_kwargs["prompt_signals"] = SemanticRouterPromptSignals.from_summary(ctx.turn_summary)
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
