"""Deterministic fresh-command switches during pending interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import _is_query_domain_request
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_beneficiary import (
    _is_beneficiary_clarification_interrupt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import _handle_switch_intent_route
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    TRANSACTION_INTENTS,
    _could_be_schedule_interrupt_read_request,
)
from banking.intent.routing_signals import looks_like_support_problem_statement
from shared.types.planner import InterruptRouteDecision


def _fresh_supported_command_target(text: str) -> tuple[str, str] | None:
    if _is_query_domain_request(text):
        return "query", "deterministic query request during pending interrupt"
    if _could_be_schedule_interrupt_read_request(text):
        return "schedule", "deterministic schedule read request during pending interrupt"
    if looks_like_support_problem_statement(text):
        return "support", "deterministic support issue request during pending interrupt"
    return None


async def _fresh_command_switch_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if _is_beneficiary_clarification_interrupt(interrupt):
        return None
    if not runtime.current_task_types or not runtime.current_task_types.issubset(TRANSACTION_INTENTS):
        return None

    target = _fresh_supported_command_target(runtime.text)
    if target is None:
        return None

    target_intent, reason = target
    logger.info(
        "interrupt_deterministic_fresh_command_switch",
        kind=interrupt.kind,
        active_type=runtime.active_type,
        target_intent=target_intent,
        tasks=interrupt.task_ids,
    )
    return await _handle_switch_intent_route(
        state=state,
        interrupt=interrupt,
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=1.0,
            detected_language=None,
            target_intent=target_intent,
            target_mode="new",
            reason=reason,
        ),
        task_planner=runtime.task_planner,
        text=runtime.text,
        active_type=runtime.active_type,
        current_task_types=runtime.current_task_types,
        services=runtime.services,
    )


__all__ = ["_fresh_command_switch_updates"]
