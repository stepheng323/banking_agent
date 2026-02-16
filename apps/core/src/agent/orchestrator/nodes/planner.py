import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
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

    task_planner = config["configurable"].get("task_planner")
    text = state.last_message_text or ""

    redis_client = config["configurable"].get("redis_client")
    planner_context_parts: list[str] = []

    if redis_client:
        try:
            import asyncio

            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            query_session_key = f"query:session:{state.phone_number}"

            # Pre-check transactional keywords to skip query session processing
            transactional_keywords = {"send", "transfer", "pay", "airtime", "data", "buy", "recharge", "topup"}
            message_tokens = set(re.findall(r"[a-z0-9']+", text.lower()))
            is_transactional = bool(message_tokens & transactional_keywords)

            # Parallel Redis fetch
            suggestion_data, query_session_data = await asyncio.gather(
                redis_client.get(suggestion_key),
                redis_client.get(query_session_key) if not is_transactional else asyncio.sleep(0),
            )

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

            if not is_transactional and query_session_data:
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
            elif is_transactional:
                logger.info("planner_query_context_skipped", reason="transactional_keywords_detected")
        except Exception as e:
            logger.warning("planner_context_check_failed", error=str(e))

    active_intent = None
    if state.waves:
        try:
            current_wave = state.waves[state.current_wave_index]
            if current_wave:
                t_id = current_wave[0]
                if t_id in state.tasks:
                    active_task = state.tasks[t_id]
                    active_intent = active_task.type

                    payload_view = {
                        k: v for k, v in active_task.payload.items() if k not in ["result", "error", "confirmation"]
                    }

                    planner_context_parts.append(
                        f"Active Flow: {active_intent.upper()} (User is currently in this flow).\n"
                        f"Current Task Data: {payload_view}\n"
                        "Review Rule 9 (CONTEXT OVERRIDE):"
                        f"- If input is slot-filling or update (e.g. 'Mum', '5k'), KEEP intent='{active_intent}'.\n"
                        "- If input is CLEARLY unrelated (e.g. 'Show beneficiaries', 'Balance'), CHANGE intent to new one."
                    )
                    logger.info("planner_context_active_flow_injected", intent=active_intent)
        except Exception as e:
            logger.warning("active_flow_context_failed", error=str(e))

    # [NEW] Context Manager Integration (Pattern A)
    # Inject short-term memory (transactions, beneficiaries, etc.)
    from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager

    ctx_manager = OrchestratorContextManager()
    short_term_context = ctx_manager.build_llm_summary(state)

    if short_term_context:
        planner_context_parts.append(short_term_context)
        logger.info("planner_context_injected", context="short_term_memory")

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

    # [NEW] Handle Cancellation Explicitly
    if getattr(planner_output, "is_cancellation", False) or planner_output.primary_intent == "cancel":
        logger.info("planner_cancellation_detected", intent=planner_output.primary_intent)
        return {"waves": [], "final_response": planner_output.response or "Cancelled."}

    if not planner_output or not planner_output.tasks:
        # If no tasks, verify if we should switch context or pass-through
        # E.g. "Hi" -> conversational -> no tasks
        if state.waves and planner_output and planner_output.primary_intent != "conversational":
            # If planner sees a structured intent but 0 tasks, it might be a cancellation or error
            # If intent differs from active, we probably want to clear waves
            if planner_output.primary_intent != active_intent:
                logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
                return {"waves": [], "final_response": planner_output.response}

        if planner_output and planner_output.primary_intent == "conversational":
            message, handoff = await generate_meta_reply(
                task_planner.planner_llm if task_planner else None,
                user_message=text,
                user_language_hint=state.loaded_context.get("language"),
                active_session=None,
            )
            if handoff == "meta" and message:
                return {"final_response": message}
        return {"final_response": planner_output.response if planner_output else "I didn't understand."}

    # [NEW] Decision: Switch vs Pass-through
    if state.waves and active_intent:
        # If intent matches, assume slot-filling/update and let Extractor handle it
        # UNLESS it's a "mixed" intent (which might add tasks)
        if planner_output.primary_intent == active_intent and planner_output.primary_intent != "mixed":
            logger.info("planner_intent_match_active", intent=active_intent, action="pass_through")
            return {}

        # If intent differs (e.g. Transfer -> Beneficiary), we Switch.
        logger.info("planner_intent_switch", old=active_intent, new=planner_output.primary_intent)
        # Proceed to generate new tasks (which will overwrite active waves)

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

        if plan_item.executor in ("transfer", "airtime", "data"):
            payload["skip_extraction"] = True

        # Map planner's generic field names to TransferPayload field names
        if plan_item.executor == "transfer":
            if "recipient" in payload:
                recipient_val = payload.pop("recipient")
                if recipient_val:
                    recipient_val = recipient_val.rstrip("},. ")
                if not payload.get("recipient_name"):
                    payload["recipient_name"] = recipient_val

            from shared.utils.narration import format_narration

            payload["narration"] = format_narration(
                payload.get("narration"), payload.get("recipient_resolved_name") or payload.get("recipient_name")
            )

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
