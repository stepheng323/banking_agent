from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.chat.src.agent.orchestrator.capabilities.llm import CapabilityClassifierLLM
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView
from apps.chat.src.agent.orchestrator.workflows.gate.utils.router_context import _should_invoke_semantic_router
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouterLLM
from apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session import (
    _load_query_session_snapshot,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary import (
    get_or_build_turn_context_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_types import (
    TurnContextSummary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner


@dataclass
class GateContext:
    """Shared context threaded through all gate stages."""

    state: OrchestratorState
    config: RunnableConfig
    redis_client: redis.Redis | None
    task_planner: TaskPlanner | None
    semantic_router_llm: SemanticRouterLLM | None
    capability_classifier_llm: CapabilityClassifierLLM | None
    conversation_responder: ConversationResponder | None
    state_view: GateStateView
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
    routing_hints: list[dict[str, str]] = field(default_factory=list)

    _query_loaded: bool = False
    _summary_loaded: bool = False
    _support_context_loaded: bool = False
    support_context: Any | None = None

    async def ensure_support_context(self) -> Any | None:
        """Lazily probe Redis for the support context."""
        if self._support_context_loaded:
            return self.support_context
        self._support_context_loaded = True
        if self.redis_client is None:
            return None
        from apps.chat.src.agent.orchestrator.workflows.gate.utils.support_identity import _support_user_id
        from banking.support.context_manager import SupportContextManager
        try:
            self.support_context = await SupportContextManager(self.redis_client).get(
                _support_user_id(self.state_view)
            )
        except Exception:
            self.support_context = None
        return self.support_context

    def add_routing_hint(self, *, domain: str, reason: str, source: str) -> None:
        """Record a non-authoritative routing hint for later semantic/planner stages."""
        hint = {"domain": domain, "reason": reason, "source": source}
        if hint not in self.routing_hints:
            self.routing_hints.append(hint)

    def has_routing_hint(self, domain: str) -> bool:
        return any(hint.get("domain") == domain for hint in self.routing_hints)

    async def has_active_query_session(self) -> bool:
        await self.ensure_query_session()
        return bool(
            (isinstance(self.query_session_snapshot, dict) and self.query_session_snapshot.get("session_active"))
            or self.state_view.has_session_for_domain("query")
        )

    async def defer_active_query_session_to_semantic_router(self, *, source: str) -> bool:
        if self.task_planner is None or not await self.has_active_query_session():
            return False
        self.add_routing_hint(domain="query", reason="active_query_session", source=source)
        return True

    async def ensure_query_session(self) -> None:
        """Lazily project the active query surface or compact stashed compatibility state."""
        if self._query_loaded:
            return
        self._query_loaded = True
        self.query_session_snapshot, self.query_session_source = await _load_query_session_snapshot(self.state_view)

    async def ensure_turn_summary(self) -> None:
        """Lazily compute the turn context summary."""
        await self.ensure_query_session()
        if self._summary_loaded:
            return
        self._summary_loaded = True

        should_invoke = _should_invoke_semantic_router(self.message_text)
        summary_path_label = (
            "interrupt_path"
            if self.live_pending_interrupt
            else (
                "direct_path"
                if (not self.state_view.has_quote and self.task_planner is not None and should_invoke)
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
