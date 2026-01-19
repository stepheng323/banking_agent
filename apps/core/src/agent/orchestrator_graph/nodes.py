"""Nodes for the Orchestrator Graph."""

from typing import Literal

from apps.core.src.agent.orchestrator_graph.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


from typing import Literal, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator_graph.state import OrchestratorState
from apps.core.src.agent.workflow.inputs import is_cancellation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def session_gate(state: OrchestratorState) -> dict:
    """
    Router node.
    Decides: Resume (Callback/Input) vs New Request vs Cancel.
    
    Updates state flags and prepares for routing.
    """
    logger.info("session_gate_check", user=state.phone_number)
    
    updates = {}
    
    # 1. Check for Flow Callback (Auth Resume)
    if state.flow_callback:
        logger.info("session_gate_callback_received", user=state.phone_number)
        return {
            "pin_verified": True, 
            "awaiting_auth": False,
            "flow_callback": None # Consume it
        }

    # 2. Check for Cancellation
    if state.last_message_text and is_cancellation(state.last_message_text):
         logger.info("session_gate_cancellation", user=state.phone_number)
         return {
             "waves": [],
             "current_wave_index": 0,
             "task_status": {},
             "task_results": {},
             "awaiting_auth": False,
             "awaiting_input": False,
             "planner_output": None,
             "pin_verified": False,
             "final_response": "Cancelled.",
         }

    # 3. Check for Expected Input
    if state.awaiting_input:
        logger.info("session_gate_input_resume", user=state.phone_number)
        # We assume the last message IS the input.
        # Logic to apply this input happens in 'preflight' or a dedicated 'apply_input' node?
        # The plan had 'preflight' re-check.
        # Ideally, we should capture it here or let preflight handle it.
        # We'll clear the flag here so preflight runs strictly.
        # Actually, if we clear logic here, preflight might fail again?
        # No, preflight calls "prepare". The ADAPTER needs the input.
        # Where does the adapter get the input? From `state.last_message_text`.
        return {"awaiting_input": False}

    # 4. Default: No-op (Routing will decide based on 'waves' existence)
    return {}


async def plan_or_resume(state: OrchestratorState, config: RunnableConfig) -> dict:
    """
    Planning node.
    If resuming -> no-op.
    If new -> Call Planner LLM -> Build Waves.
    """
    logger.info("plan_or_resume_check")
    
    # 1. Resume Check
    if state.waves:
        logger.info("plan_or_resume_skipping_existing_plan")
        return {}
    
    # 2. Get Dependencies
    task_planner = config["configurable"].get("task_planner")
    if not task_planner:
        logger.error("plan_or_resume_missing_planner")
        return {"final_response": "System Error: Planner not configured."}
    
    # 3. Call Planner
    # We pass the instruction text.
    text = state.last_message_text or ""
    planner_output = await task_planner.plan_tasks(state.phone_number, text)
    
    if not planner_output or not planner_output.tasks:
        # Conversational / No tasks
        response = planner_output.response if planner_output else "I didn't understand that."
        return {"final_response": response}
    
    # 4. Build Waves (Topological Sort)
    from apps.core.src.agent.workflow.dag_executor import WorkflowDAGExecutor
    # We can reuse the static method or logic from DAG Executor if refactored, 
    # but for now let's reuse the logic.
    # Actually WorkflowDAGExecutor.compute_waves isn't static.
    # We can reproduce the logic here or make it static.
    # For speed, reproduced logic (it's standard Kahn's algo).
    
    tasks = planner_output.tasks
    task_map = {t.task_id: t for t in tasks}
    final_waves = []
    
    # Simple dependency graph
    incoming = {t.task_id: set(t.dependencies) for t in tasks}
    
    while incoming:
        # tasks with no remaining deps
        wave = [tid for tid, deps in incoming.items() if not deps]
        if not wave:
            # Cycle detected
            logger.error("cycle_detected_in_plan")
            break
            
        final_waves.append(wave)
        
        # remove from graph
        for tid in wave:
            del incoming[tid]
            
        # remove deps
        for deps in incoming.values():
            deps.difference_update(wave)
            
    # initialize status
    initial_status = {t.task_id: "pending" for t in tasks}
    
    return {
        "planner_output": planner_output,
        "waves": final_waves,
        "current_wave_index": 0,
        "task_status": initial_status,
        "task_results": {},
        "normalized_instruction": text,
    }


async def preflight_wave(state: OrchestratorState, config: RunnableConfig) -> dict:
    """
    Pre-execution check.
    Calls adapter.prepare() for tasks in current wave.
    Aggregates missing inputs.
    """
    current_idx = state.current_wave_index
    if current_idx >= len(state.waves):
        logger.info("preflight_wave_out_of_bounds", index=current_idx)
        return {} # Should advance check catch this?

    wave_tasks = state.waves[current_idx]
    logger.info("preflight_wave_start", wave=current_idx, tasks=wave_tasks)
    
    adapter_factory = config["configurable"].get("adapter_factory")
    
    missing_agg = {}
    pending_questions = []
    
    for tid in wave_tasks:
        # Get task details (from planner output)
        if not state.planner_output:
             continue
        task = next((t for t in state.planner_output.tasks if t.task_id == tid), None)
        if not task:
            continue
            
        # Get adpater
        if adapter_factory:
            adapter = adapter_factory.get_adapter(task.executor)
            if adapter:
                # Prepare
                result = await adapter.prepare(task, state)
                
                # Check missing
                if not result.get("ready", False):
                    missing = result.get("missing_fields", [])
                    if missing:
                        missing_agg[tid] = missing
                        # Simple question generation (Adapter could provide this too)
                        pending_questions.append(f"Requesting info for {task.executor}: {', '.join(missing)}")
    
    if missing_agg:
        logger.info("preflight_wave_missing_input", missing=missing_agg)
        combined_q = "I need a bit more info: " + "; ".join(pending_questions)
        return {
            "awaiting_input": True,
            "pending_fields_by_task": missing_agg,
            "pending_question": combined_q,
            "final_response": combined_q # This will be sent to user
        }
        
    return {"awaiting_input": False}


async def auth_gate(state: OrchestratorState, config: RunnableConfig) -> dict:
    """
    Authorization check.
    If current wave has money moves & not verified -> Trigger Auth Flow.
    """
    from apps.core.src.agent.workflow.models import TaskRisk, get_task_risk
    
    current_idx = state.current_wave_index
    if current_idx >= len(state.waves):
        return {}
        
    wave_tasks = state.waves[current_idx]
    
    # Check for Money Move
    needs_auth = False
    for tid in wave_tasks:
        if not state.planner_output: continue
        task = next((t for t in state.planner_output.tasks if t.task_id == tid), None)
        if not task: continue
        
        risk = get_task_risk(task.executor)
        if risk == TaskRisk.MONEY_MOVE:
            needs_auth = True
            break
            
    if needs_auth and not state.pin_verified:
        logger.info("auth_gate_auth_required")
        
        # Trigger Flow Side Effect
        whatsapp_client = config["configurable"].get("whatsapp_client")
        if whatsapp_client:
            # Checkpoint ID is usually available in config if using LangGraph checkpointer
            # or we generate a flow token.
            # Ideally we pass a token or use the thread_id.
            thread_id = config["configurable"].get("thread_id") or state.phone_number
            # Send Flow
            # For now, just logging stub or actual send call if simple
            # We need to construct the flow payload.
            # Using a simplified trigger for now.
            logger.info("triggering_auth_flow", phone=state.phone_number)
            # await whatsapp_client.send_auth_flow(...) # TODO: Implement actual call
            pass
            
        return {
            "awaiting_auth": True,
            "final_response": "Please authorize this transaction.", # Fallback text
        }
        
    return {"awaiting_auth": False}


async def execute_wave_parallel(state: OrchestratorState, config: RunnableConfig) -> dict:
    """
    Execution node.
    Runs tasks in current wave concurrently.
    """
    import asyncio
    from apps.core.src.agent.workflow.models import TaskStatus
    
    current_idx = state.current_wave_index
    if current_idx >= len(state.waves):
        return {}
        
    wave_tasks = state.waves[current_idx]
    logger.info("execute_wave_start", wave=current_idx, tasks=wave_tasks)
    
    adapter_factory = config["configurable"].get("adapter_factory")
    if not adapter_factory:
        logger.error("adapter_factory_missing")
        return {}

    # Prepare execution coroutines
    coroutines = []
    task_ids = []
    
    for tid in wave_tasks:
        if not state.planner_output: continue
        task = next((t for t in state.planner_output.tasks if t.task_id == tid), None)
        if not task: continue
        
        adapter = adapter_factory.get_adapter(task.executor)
        if adapter:
            task_ids.append(tid)
            coroutines.append(adapter.execute(task, state))
            
    # Execute
    results = await asyncio.gather(*coroutines, return_exceptions=True)
    
    # Process results
    new_results = {}
    new_statuses = {}
    
    for tid, res in zip(task_ids, results):
        if isinstance(res, Exception):
            logger.error("task_execution_error", task_id=tid, error=str(res))
            # Mark failed
            new_statuses[tid] = TaskStatus.FAILED
            # Create error result object? Or adapter handles it?
            # Adapter should ideally capture exception return FAILED TaskResult
            # For now, minimal fallback if adapter crashed
        else:
            new_results[tid] = res
            new_statuses[tid] = res.status
            
    # Merge updates
    # Note: State updates in LangGraph are partial dictionary merges usually, 
    # but for nested dicts (task_results), we might need to fetch old and update?
    # Or return the full dict?
    # LangGraph merges top-level keys.
    # So we should fetch current and update.
    updated_results = state.task_results.copy()
    updated_results.update(new_results)
    
    updated_statuses = state.task_status.copy()
    updated_statuses.update(new_statuses)
    
    return {
        "task_results": updated_results,
        "task_status": updated_statuses,
    }


async def advance_or_finish(state: OrchestratorState) -> dict:
    """
    Progress tracking.
    Increments wave index or marks complete.
    """
    next_idx = state.current_wave_index + 1
    logger.info("advance_wave", next=next_idx)
    return {"current_wave_index": next_idx}


async def summary_node(state: OrchestratorState) -> dict:
    """
    Generates final user response.
    """
    from apps.core.src.agent.workflow.models import TaskStatus, WorkflowStatus
    
    responses = []
    for res in state.task_results.values():
        if res.status == TaskStatus.COMPLETED and res.data.get("response"):
            responses.append(res.data["response"])
            
    if responses:
        final = "\n\n".join(responses)
    else:
        # Check failures
        failed = [tid for tid, s in state.task_status.items() if s == TaskStatus.FAILED]
        if failed:
            final = f"Some tasks failed: {', '.join(failed)}"
        else:
            final = "Done!"
            
    return {"final_response": final}
