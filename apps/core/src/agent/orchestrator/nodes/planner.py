from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
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

    redis_client = config["configurable"].get("redis_client")
    planner_context_parts: list[str] = []

    if redis_client:
        try:
            # Check for pending beneficiary suggestion
            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            suggestion_data = await redis_client.get(suggestion_key)
            if suggestion_data:
                import json

                data = json.loads(suggestion_data)
                name = data.get("recipient_name") or data.get("alias_suggested") or "Unknown"
                planner_context_parts.append(
                    f"Active Context: User was asked to save beneficiary '{name}'.\n"
                    f"- Reply 'yes' -> Save with name '{name}'\n"
                    f"- Reply 'Bob' (or any name) -> Save with alias 'Bob'"
                )
                logger.info("planner_context_injected", context="beneficiary_suggestion")

            # Check for active query session (continuation context)
            query_session_key = f"query:session:{state.phone_number}"
            query_session_data = await redis_client.get(query_session_key)
            if query_session_data:
                import json

                session = json.loads(query_session_data)
                summary_text = None
                query_result = session.get("query_result")
                if isinstance(query_result, dict):
                    summary_text = query_result.get("summary_text")
                summary_snippet = f' Last summary: "{summary_text[:200]}".' if summary_text else ""
                planner_context_parts.append(
                    "Active Query Session: The user recently viewed transaction results."
                    f"{summary_snippet}\n"
                    "- If the user asks to continue (e.g., 'more', 'next', 'show transactions', 'details', 'receipt', 'issue')"
                    " or adjusts time/filters, create a query task (executor='query') so the continuation handler can process it.\n"
                    "- If the user asks a fresh query (e.g., 'show my recent transactions'), still create a query task as a new query."
                )
                logger.info("planner_context_injected", context="query_session")
        except Exception as e:
            logger.warning("planner_context_check_failed", error=str(e))

    planner_context = "\n\n".join(planner_context_parts) if planner_context_parts else "None"

    try:
        planner_output = await task_planner.plan_tasks(state.phone_number, text, context=planner_context)
        logger.info("planner_tasks_generated", output=planner_output)

        if redis_client and planner_context != "None" and planner_output and planner_output.tasks:
            is_saving = any(
                t.executor == "beneficiary" and t.action == "save_beneficiary" for t in planner_output.tasks
            )
            if not is_saving:
                suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
                await redis_client.delete(suggestion_key)
                logger.info("cleared_stale_beneficiary_context", phone=state.phone_number)

    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return {"final_response": "I'm having trouble understanding. Could you rephrase?"}

    if not planner_output or not planner_output.tasks:
        return {"final_response": planner_output.response if planner_output else "I didn't understand."}

    new_tasks = {}
    wave_tasks = []

    for plan_item in planner_output.tasks:
        payload = plan_item.parameters.model_dump() if plan_item.parameters else {}

        if plan_item.action:
            payload.setdefault("action", plan_item.action)
        if plan_item.instruction:
            payload.setdefault("instruction", plan_item.instruction)

        if plan_item.executor == "query" and not payload.get("message"):
            payload["message"] = plan_item.instruction or text

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
