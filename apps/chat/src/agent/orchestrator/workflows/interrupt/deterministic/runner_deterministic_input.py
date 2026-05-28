"""Deterministic input interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import (
    _continue_flow_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import (
    _input_greeting_reprompt_text,
    _reprompt_or_reset_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_selection_route import (
    _resolve_deterministic_input_selection_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_route import (
    _resolve_deterministic_input_slot_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    TRANSACTION_INTENTS,
    _input_interrupt_required_fields,
    _is_input_interrupt_greeting,
)


def _input_shortcut_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    for route in (
        _resolve_deterministic_input_selection_route(state=state, interrupt=interrupt, text=runtime.text),
        _resolve_deterministic_input_slot_route(state=state, interrupt=interrupt, text=runtime.text),
    ):
        if route is None:
            continue
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            reason=route.reason,
        )
        return _continue_flow_updates(state, interrupt)
    return None


async def _input_greeting_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if not (
        interrupt.kind == "input"
        and _is_input_interrupt_greeting(runtime.text)
        and runtime.current_task_types.intersection(TRANSACTION_INTENTS)
    ):
        return None

    logger.info(
        "interrupt_input_greeting_nudge",
        task_ids=interrupt.task_ids,
        required_fields=sorted(_input_interrupt_required_fields(interrupt)),
    )
    return await _reprompt_or_reset_updates(
        state,
        interrupt,
        runtime.redis_client,
        prompt_override=_input_greeting_reprompt_text(state, interrupt, runtime.current_task_types),
    )


__all__ = [
    "_input_greeting_updates",
    "_input_shortcut_updates",
]
