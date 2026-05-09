from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.reprompt import _reprompt_updates
from shared.utils.logging import get_logger

logger = get_logger(__name__)
TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320

def _approve_auth_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    if not state.pin_verified and interrupt.auth_method == "pin":
        logger.info("auth_approval_requires_verified_pin", tasks=interrupt.task_ids)
        return _reprompt_updates(state, interrupt)

    new_tasks = state.tasks.copy()
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        task.stage = TaskStage.EXECUTING
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }
