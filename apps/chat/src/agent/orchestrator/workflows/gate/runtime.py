"""Typed runtime construction for the gate workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.interrupt_state import _has_live_pending_interrupt
from apps.chat.src.agent.orchestrator.workflows.gate.language import _allow_phrase_heavy_fastpath
from apps.chat.src.agent.orchestrator.workflows.gate.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.state_view import GateStateView, gate_state_view


@dataclass(frozen=True)
class GateDependencies:
    """Runtime dependencies available to gate stages."""

    redis_client: Any | None
    task_planner: Any | None
    conversation_responder: Any | None

    @classmethod
    def from_configurable(cls, configurable: Mapping[str, Any]) -> GateDependencies:
        return cls(
            redis_client=configurable.get("redis_client"),
            task_planner=configurable.get("task_planner"),
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
            conversation_responder=self.dependencies.conversation_responder,
            state_view=self.state_view,
            message_text=self.message_text,
            current_locale=self.current_locale,
            gate_updates={},
            live_pending_interrupt=self.live_pending_interrupt,
            phrase_heavy_fastpath_allowed=self.phrase_heavy_fastpath_allowed,
        )


def build_gate_runtime(state: OrchestratorState, config: RunnableConfig) -> GateRuntime:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        configurable = {}

    state_view = gate_state_view(state)
    message_text = state_view.message_text
    current_locale = _current_locale(state)
    return GateRuntime(
        state=state,
        config=config,
        dependencies=GateDependencies.from_configurable(configurable),
        state_view=state_view,
        message_text=message_text,
        current_locale=current_locale,
        live_pending_interrupt=_has_live_pending_interrupt(state),
        phrase_heavy_fastpath_allowed=_allow_phrase_heavy_fastpath(message_text, current_locale),
    )


__all__ = ["GateDependencies", "GateRuntime", "build_gate_runtime"]
