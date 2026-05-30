from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale
from banking.presentation.i18n.renderer import render_message


def _build_confirmation_scope_clarification_outbox(state: OrchestratorState) -> list[dict[str, Any]]:
    locale = _state_locale(state)
    return [{"type": "say", "text": render_message("transfer.resolve.which_recipient", locale)}]


__all__ = ["_build_confirmation_scope_clarification_outbox"]
