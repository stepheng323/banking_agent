from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def plan_tasks(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Planner Node.

    1. If new request (no active waves), call Planner to create TaskSpecs.
    2. If existing waves, this is a pass-through (or bulk extraction update).
    """
    if state.waves and state.pending_interrupt is None:
        return {}

    if state.waves:
        return {}  # Don't disrupt existing plan mid-flight for now

    task_planner = config["configurable"].get("task_planner")
    text = state.last_message_text or ""

    try:
        planner_output = await task_planner.plan_tasks(state.phone_number, text)
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return {"final_response": "I'm having trouble understanding. Could you rephrase?"}

    if not planner_output or not planner_output.tasks:
        return {"final_response": planner_output.response if planner_output else "I didn't understand."}

    new_tasks = {}
    wave_tasks = []

    for plan_item in planner_output.tasks:
        payload = plan_item.parameters.model_dump() if plan_item.parameters else {}

        spec = TaskSpec(
            id=plan_item.task_id,
            type=plan_item.executor,
            stage=TaskStage.DRAFT,
            payload=payload,
        )
        new_tasks[spec.id] = spec
        wave_tasks.append(spec.id)

    return {
        "tasks": new_tasks,
        "waves": [wave_tasks],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
    }
