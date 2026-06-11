from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _approve_auth_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    if interrupt.auth_method == "pin":
        interrupt_keys = {key for key in getattr(interrupt, "authorized_task_idempotency_keys", []) if key}
        authorization_idempotency_key = getattr(interrupt, "authorization_idempotency_key", None)
        if authorization_idempotency_key:
            interrupt_keys.add(authorization_idempotency_key)
        auth_context = state.authorization_context
        if (
            not state_view.pin_verified
            or auth_context is None
            or not interrupt_keys
            or not interrupt_keys.issubset(auth_context.authorized_keys())
        ):
            logger.info("auth_approval_requires_verified_pin", tasks=interrupt.task_ids)
            return _reprompt_updates(state, interrupt)

    new_tasks = state_view.task_map_copy()
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        confirmation = task.payload.get("confirmation")
        if isinstance(confirmation, dict):
            confirmation["confirmed"] = True
        else:
            task.payload["confirmation"] = {"confirmed": True}
        task.stage = TaskStage.EXECUTING
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }
