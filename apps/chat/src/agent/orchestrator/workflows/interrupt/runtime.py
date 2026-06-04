from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _active_intent, _current_task_types_for_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import (
    InterruptStateView,
    interrupt_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices


@dataclass(frozen=True)
class InterruptRuntime:
    state_view: InterruptStateView
    interrupt: Any
    text: str
    current_task_types: set[str]
    active_type: str
    task_planner: Any
    redis_client: Any | None
    services: OrchestrationServices


def build_interrupt_runtime(
    *,
    state: OrchestratorState,
    config: RunnableConfig,
) -> InterruptRuntime:
    state_view = interrupt_state_view(state)
    interrupt = state_view.pending_interrupt
    if interrupt is None:
        raise RuntimeError("pending_interrupt_required")
    current_task_types = _current_task_types_for_view(state_view, interrupt.task_ids)
    runtime_config = OrchestrationConfig.from_runnable_config(config)
    return InterruptRuntime(
        state_view=state_view,
        interrupt=interrupt,
        text=state_view.message_text,
        current_task_types=current_task_types,
        active_type=_active_intent(current_task_types),
        task_planner=runtime_config.get("task_planner"),
        redis_client=runtime_config.get("redis_client"),
        services=runtime_config.services(),
    )


__all__ = ["InterruptRuntime", "build_interrupt_runtime"]
