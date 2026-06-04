from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _active_intent, _current_task_types
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices


@dataclass(frozen=True)
class InterruptRuntime:
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
    interrupt = state.pending_interrupt
    if interrupt is None:
        raise RuntimeError("pending_interrupt_required")
    current_task_types = _current_task_types(state, interrupt.task_ids)
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        configurable = {}
    raw_services = cast(OrchestrationServices | Mapping[str, object] | None, configurable.get("services"))
    return InterruptRuntime(
        interrupt=interrupt,
        text=state.last_message_text or "",
        current_task_types=current_task_types,
        active_type=_active_intent(current_task_types),
        task_planner=configurable.get("task_planner"),
        redis_client=configurable.get("redis_client"),
        services=OrchestrationServices.from_mapping(raw_services),
    )


__all__ = ["InterruptRuntime", "build_interrupt_runtime"]
