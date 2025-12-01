
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.core.src.agent.orchestrator.flow_completion_callback import OrchestratorFlowCompletionCallback
from apps.core.src.agent.models.planner import PlannerOutput, PlannedTask
from shared.types.agent_types import TaskStatus

async def test_batch_auth_flow_v2():
    # Mock dependencies
    mock_task_queue_service = AsyncMock()
    mock_orchestrator = MagicMock()
    mock_orchestrator.whatsapp_client = AsyncMock()
    mock_orchestrator.context_manager = AsyncMock()
    mock_orchestrator.task_planner = AsyncMock()
    
    # Create Callback
    callback = OrchestratorFlowCompletionCallback(
        task_queue_service=mock_task_queue_service,
        orchestrator=mock_orchestrator
    )
    
    phone_number = "1234567890"
    
    # Setup Tasks
    task1 = PlannedTask(
        id="task_1",
        action="transfer",
        executor="transfer",
        instruction="Transfer 5000 to Ayo",
        parameters={"amount": 5000, "recipient": "Ayo"},
        depends_on=[],
        status=TaskStatus.COLLECTION_COMPLETE
    )
    
    planner_output = PlannerOutput(
        normalized_instruction="Transfer 5000 to Ayo",
        primary_intent="transfer",
        tasks=[task1],
        notes=""
    )
    
    # --- Test Case: Batch Summary with Source Account ---
    print("\n--- Test Case: Batch Summary with Source Account ---")
    
    # Mock queue state
    mock_task_queue_service.has_active_queue.return_value = True
    mock_task_queue_service.get_current_task.return_value = "task_1"
    mock_task_queue_service.get_task_queue.return_value = planner_output
    
    # Mock results with source account
    mock_task_queue_service.get_task_results.return_value = {
        "task_1": {
            "status": TaskStatus.COLLECTION_COMPLETE.value, 
            "result": {
                "recipient_account": "1234567890",
                "recipient_bank_name": "Access Bank",
                "account_resolved": {"account_name": "Ayo"},
                "selected_source_account": {
                    "bank_name": "GTBank",
                    "account_number": "0123456789"
                }
            }
        }
    }
    
    # Mock completed/done tasks
    mock_task_queue_service.get_completed_task_ids.return_value = []
    
    # Execute callback for Task 1
    await callback.on_flow_complete(
        phone_number, 
        "transfer", 
        {"status": "collection_complete"}
    )
    
    # Verify summary message
    calls = mock_orchestrator.whatsapp_client.send_flow.call_args_list
    print(f"WhatsApp flow calls: {calls}")
    
    summary_sent = False
    for call in calls:
        kwargs = call.kwargs
        if "text_body" in kwargs:
            msg = kwargs["text_body"]
            # Check for source account info
            if "from GTBank (6789)" in msg:
                print(f"✅ PASS: Batch summary includes source account: '{msg[:50]}...'")
                summary_sent = True
                break
            
    if not summary_sent:
        print("❌ FAIL: Batch summary does not include source account")

if __name__ == "__main__":
    asyncio.run(test_batch_auth_flow_v2())
