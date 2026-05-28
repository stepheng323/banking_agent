"""Gate workflow runner."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.interrupt_state import _has_live_pending_interrupt
from apps.chat.src.agent.orchestrator.workflows.gate.language import _allow_phrase_heavy_fastpath
from apps.chat.src.agent.orchestrator.workflows.gate.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def session_gate_direct_path(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.workflows.gate.registry import (
        _GATE_STAGES,
        _stage_stale_interrupt_cleanup,
    )

    """
    Direct-path gate.

    Applies deterministic guardrails first, then delegates first-pass semantic routing
    to the semantic router, and falls through to planner only for planner-owned routes.

    Architecture: a pipeline of focused stage functions. Each stage returns a dict
    (short-circuit with a gate response) or None (continue to the next stage).
    """
    session = state.session_stack[-1] if state.session_stack else None
    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    message_text = (state.last_message_text or "").strip()
    current_locale = _current_locale(state)
    ctx = GateContext(
        state=state,
        config=config,
        redis_client=config["configurable"].get("redis_client"),
        task_planner=config["configurable"].get("task_planner"),
        conversation_responder=config["configurable"].get("conversation_responder"),
        message_text=message_text,
        current_locale=current_locale,
        gate_updates={},
        live_pending_interrupt=_has_live_pending_interrupt(state),
        phrase_heavy_fastpath_allowed=_allow_phrase_heavy_fastpath(message_text, current_locale),
    )

    _stage_stale_interrupt_cleanup(ctx)

    for stage in _GATE_STAGES:
        result = await stage(ctx)
        if result is not None:
            return result

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **_route_observability_updates(owner="planner", decision="planner_handoff"),
    }
