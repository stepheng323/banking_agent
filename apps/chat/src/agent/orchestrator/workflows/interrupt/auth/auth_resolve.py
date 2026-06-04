from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _approve_auth_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    if not state_view.pin_verified and interrupt.auth_method == "pin":
        logger.info("auth_approval_requires_verified_pin", tasks=interrupt.task_ids)
        return _reprompt_updates(state, interrupt)

    new_tasks = state_view.task_map_copy()
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        task.stage = TaskStage.EXECUTING
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }
