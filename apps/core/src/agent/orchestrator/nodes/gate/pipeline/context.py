from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner.context import (
    TurnContextSummary,
    _load_query_session_snapshot,
    get_or_build_turn_context_summary,
)


@dataclass
class GateContext:
    """Shared context threaded through all gate stages."""

    state: OrchestratorState
    config: RunnableConfig
    redis_client: Any | None
    task_planner: Any | None
    conversation_responder: Any | None
    message_text: str
    current_locale: str
    gate_updates: dict[str, Any]
    live_pending_interrupt: bool
    phrase_heavy_fastpath_allowed: bool
    ambiguous_banking_domain: str | None = None
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None
    turn_summary: TurnContextSummary | None = None
    summary_updates: dict[str, Any] | None = None

    # Callback to evaluate semantic router to prevent circular imports
    should_invoke_semantic_router_fn: Any | None = None

    _query_loaded: bool = False
    _summary_loaded: bool = False

    async def ensure_query_session(self) -> None:
        """Lazily load the query session snapshot from Redis/state."""
        if self._query_loaded:
            return
        self._query_loaded = True
        self.query_session_snapshot, self.query_session_source = await _load_query_session_snapshot(
            self.state, self.redis_client
        )

    async def ensure_turn_summary(self) -> None:
        """Lazily compute the turn context summary (requires query session)."""
        await self.ensure_query_session()
        if self._summary_loaded:
            return
        self._summary_loaded = True

        should_invoke = False
        if self.should_invoke_semantic_router_fn is not None:
            should_invoke = self.should_invoke_semantic_router_fn(self.message_text)

        summary_path_label = (
            "interrupt_path"
            if self.live_pending_interrupt
            else (
                "direct_path"
                if (
                    not self.state.has_quote
                    and callable(getattr(self.task_planner, "route_semantic_turn", None))
                    and should_invoke
                )
                else "planner_path"
            )
        )
        self.turn_summary, raw_updates = get_or_build_turn_context_summary(
            self.state,
            query_session_snapshot=self.query_session_snapshot,
            query_session_source=self.query_session_source,
            path_label=summary_path_label,
        )
        self.summary_updates = raw_updates or {}
