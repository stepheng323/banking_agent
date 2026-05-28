"""Session stashing helpers for interrupt switch updates."""

import time
import uuid
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referents.task_memory import remember_referents_from_stashed_session
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _stash_current_session(
    state: OrchestratorState,
    *,
    interrupt: Any,
    intent: str,
) -> list[dict[str, Any]]:
    stash_id = f"stash_{uuid.uuid4().hex}"
    current_session = {
        "stash_id": stash_id,
        "tasks": state.tasks,
        "waves": state.waves,
        "current_wave_index": state.current_wave_index,
        "pending_interrupt": interrupt,
        "intent": intent,
        "stashed_at_ts": int(time.time()),
    }
    remember_referents_from_stashed_session(state, current_session)
    return cast(list[dict[str, Any]], state.stashed_sessions + [current_session])


__all__ = ["_stash_current_session"]
