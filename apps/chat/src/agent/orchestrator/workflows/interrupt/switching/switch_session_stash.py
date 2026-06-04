"""Session stashing helpers for interrupt switch updates."""

import time
import uuid
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referents.task_memory import remember_referents_from_stashed_session
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _stash_current_session(
    state: OrchestratorState,
    *,
    interrupt: Any,
    intent: str,
) -> list[dict[str, Any]]:
    stash_id = f"stash_{uuid.uuid4().hex}"
    state_view = interrupt_state_view(state)
    current_session = {
        "stash_id": stash_id,
        "tasks": state_view.tasks,
        "waves": state_view.waves,
        "current_wave_index": state_view.current_wave_index,
        "pending_interrupt": interrupt,
        "intent": intent,
        "stashed_at_ts": int(time.time()),
    }
    remember_referents_from_stashed_session(state, current_session)
    return cast(list[dict[str, Any]], state_view.stashed_sessions + [current_session])


__all__ = ["_stash_current_session"]
