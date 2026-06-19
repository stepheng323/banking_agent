"""Typed runtime construction for the gate workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.chat.src.agent.orchestrator.capabilities.llm import CapabilityClassifierLLM
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.state.interrupt_state import _has_live_pending_interrupt
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView, gate_state_view
from apps.chat.src.agent.orchestrator.workflows.gate.utils.language import _allow_phrase_heavy_fastpath
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouterLLM
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig


@dataclass(frozen=True)
class GateDependencies:
    """Runtime dependencies available to gate stages."""

    redis_client: redis.Redis | None
    task_planner: TaskPlanner | None
    semantic_router_llm: SemanticRouterLLM | None
    capability_classifier_llm: CapabilityClassifierLLM | None
    conversation_responder: ConversationResponder | None

    @classmethod
    def from_configurable(cls, configurable: Mapping[str, Any]) -> GateDependencies:
        return cls(
            redis_client=configurable.get("redis_client"),
            task_planner=configurable.get("task_planner"),
            semantic_router_llm=configurable.get("semantic_router_llm"),
            capability_classifier_llm=configurable.get("capability_classifier_llm"),
            conversation_responder=configurable.get("conversation_responder"),
        )


@dataclass(frozen=True)
class GateRuntime:
    """Typed state derived once at the LangGraph gate node boundary."""

    state: OrchestratorState
    config: RunnableConfig
    dependencies: GateDependencies
    state_view: GateStateView
    message_text: str
    current_locale: str
    live_pending_interrupt: bool
    phrase_heavy_fastpath_allowed: bool

    def build_context(self) -> GateContext:
        return GateContext(
            state=self.state,
            config=self.config,
            redis_client=self.dependencies.redis_client,
            task_planner=self.dependencies.task_planner,
            semantic_router_llm=self.dependencies.semantic_router_llm,
            capability_classifier_llm=self.dependencies.capability_classifier_llm,
            conversation_responder=self.dependencies.conversation_responder,
            state_view=self.state_view,
            message_text=self.message_text,
            current_locale=self.current_locale,
            gate_updates={},
            live_pending_interrupt=self.live_pending_interrupt,
            phrase_heavy_fastpath_allowed=self.phrase_heavy_fastpath_allowed,
        )


def build_gate_runtime(state: OrchestratorState, config: RunnableConfig) -> GateRuntime:
    runtime_config = OrchestrationConfig.from_runnable_config(config)
    state_view = gate_state_view(state)
    message_text = state_view.message_text
    current_locale = _current_locale(state_view)
    return GateRuntime(
        state=state,
        config=config,
        dependencies=GateDependencies.from_configurable(runtime_config.configurable),
        state_view=state_view,
        message_text=message_text,
        current_locale=current_locale,
        live_pending_interrupt=_has_live_pending_interrupt(state),
        phrase_heavy_fastpath_allowed=_allow_phrase_heavy_fastpath(message_text, current_locale),
    )


__all__ = ["GateDependencies", "GateRuntime", "build_gate_runtime"]
