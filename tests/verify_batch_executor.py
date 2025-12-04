
import asyncio
import sys
import os
import time
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.core.src.agent.services.batch_executor import execute_batch
from apps.core.src.agent.models.planner import PlannerOutput, PlannedTask
from shared.types.agent_types import TaskStatus

async def simulate_transfer_execution(delay, task_id):
    """Simulate a transfer taking some time."""
    print(f"  [Task {task_id}] Starting execution (delay={delay}s)...")
    await asyncio.sleep(delay)
    print(f"  [Task {task_id}] Finished execution!")
    return {"success": True, "amount": 5000, "recipient": "Test User"}

async def test_parallel_execution():
    print("\n--- Testing Parallel Batch Execution ---")
    
    # Mock dependencies
    mock_whatsapp = AsyncMock()
    mock_queue_service = AsyncMock()
    mock_transfer_service = AsyncMock()
    mock_airtime_service = AsyncMock()
    
    # Setup tasks
    task1 = PlannedTask(
        id="task_1", action="transfer", executor="transfer", 
        instruction="Transfer 1", parameters={"amount": 5000, "recipient": "User1"},
        status=TaskStatus.COLLECTION_COMPLETE
    )
    task2 = PlannedTask(
        id="task_2", action="transfer", executor="transfer", 
        instruction="Transfer 2", parameters={"amount": 2000, "recipient": "User2"},
        status=TaskStatus.COLLECTION_COMPLETE
    )
    
    # Mock queue return
    planner_output = PlannerOutput(
        normalized_instruction="Batch", primary_intent="transfer", 
        tasks=[task1, task2], notes=""
    )
    mock_queue_service.get_task_queue.return_value = planner_output
    
    # Mock task results (needed for execution)
    mock_queue_service.get_task_results.return_value = {
        "task_1": {"status": TaskStatus.COLLECTION_COMPLETE.value, "result": {}},
        "task_2": {"status": TaskStatus.COLLECTION_COMPLETE.value, "result": {}}
    }
    
    # Mock transfer service to simulate delay
    # We need to mock the graph.resume_after_pin_verification method
    mock_transfer_service.graph = MagicMock()
    
    # Create side effects for parallel execution simulation
    async def transfer_side_effect(phone, pin_verified, input_data):
        # Simulate different delays to prove parallelism
        # If sequential: 1s + 1s = 2s
        # If parallel: max(1s, 1s) = 1s
        await asyncio.sleep(1.0)
        return "Transfer Successful"
        
    mock_transfer_service.graph.resume_after_pin_verification.side_effect = transfer_side_effect
    
    # Mock graph state
    mock_state = MagicMock()
    mock_state.values = {"transfer_status": "completed", "response": "Success"}
    mock_transfer_service.graph.graph.aget_state = AsyncMock(return_value=mock_state)

    print("🚀 Starting batch execution with 2 tasks (1.0s delay each)...")
    start_time = time.time()
    
    result = await execute_batch(
        phone_number="1234567890",
        pin_verified=True,
        whatsapp_client=mock_whatsapp,
        task_queue_service=mock_queue_service,
        transfer_service=mock_transfer_service,
        airtime_service=mock_airtime_service
    )
    
    duration = time.time() - start_time
    print(f"\n⏱️  Total Execution Time: {duration:.2f}s")
    
    # Verification
    if duration < 1.5:
        print("✅ PASS: Execution was parallel (took ~1s instead of 2s)")
    else:
        print(f"❌ FAIL: Execution seemed sequential (took {duration:.2f}s)")
        
    print(f"Completed tasks: {result['completed']}/{result['total']}")
    
    # Verify progress messages
    print("\nWhatsApp Messages Sent:")
    for call in mock_whatsapp.send_text.call_args_list:
        print(f"  - {call.args[1]}")

if __name__ == "__main__":
    asyncio.run(test_parallel_execution())
