"""Interrupt reprompt update assembly."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_auth import _build_auth_reprompt_outbox
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_confirmation import (
    _build_confirmation_reprompt_outbox,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_input import (
    _build_compact_transfer_input_reprompt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.i18n.renderer import render_message


def _reprompt_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    outbox: list[dict[str, Any]] = []
    if interrupt.kind == "input":
        compact_transfer_reprompt = _build_compact_transfer_input_reprompt(state, interrupt)
        if compact_transfer_reprompt:
            logger.info("interrupt_compact_transfer_reprompt", task_ids=interrupt.task_ids)
            outbox = [{"type": "say", "text": compact_transfer_reprompt}]
        elif interrupt.prompt:
            outbox = [{"type": "say", "text": interrupt.prompt}]
    elif interrupt.kind == "confirmation":
        outbox = _build_confirmation_reprompt_outbox(state, interrupt, interrupt.task_ids)
    elif interrupt.kind == "auth":
        outbox = _build_auth_reprompt_outbox(state, interrupt)

    if not outbox:
        prompt = str(getattr(interrupt, "prompt", "") or "").strip()
        outbox = [
            {
                "type": "say",
                "text": prompt
                or render_message(
                    "conversational.clarify",
                    state_view.current_locale,
                ),
            }
        ]

    updates: dict[str, Any] = {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state_view.tasks,
    }
    updates["outbox"] = outbox
    return updates


__all__ = ["_reprompt_updates"]
