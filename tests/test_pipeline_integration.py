import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from apps.core.src.agent.orchestrator.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.models.planner import PlannerOutput, PlannedTask

@pytest.mark.asyncio
class TestPipelineIntegration:
    
    @pytest.fixture
    def mock_services(self):
        return {
            "llm": MagicMock(),
            "user_repo": MagicMock(),
            "whatsapp_client": AsyncMock(),
            "task_queue_service": AsyncMock(),
            "conversation_responder": AsyncMock(),
            "transfer_service": AsyncMock(),
            "airtime_service": AsyncMock(),
            "task_executor": AsyncMock(),
        }

    @pytest.fixture
    def orchestrator(self, mock_services):
        with patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorContextManager") as MockContextManager, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorClassificationService") as MockClassificationService, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorTaskPlanner") as MockTaskPlanner, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorBeneficiaryHandler") as MockBeneficiaryHandler, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorCancellationHandler") as MockCancellationHandler, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorIntentRouter") as MockIntentRouter, \
             patch("apps.core.src.agent.orchestrator.orchestrator.OrchestratorFlowCompletionCallback") as MockCallback:
            
            agent = OrchestratorAgent(**mock_services)
            
            # Setup mocks
            agent.context_manager = MockContextManager.return_value
            agent.context_manager.load_user_context = AsyncMock()
            agent.context_manager.save_last_response = AsyncMock()
            agent.context_manager.clear_conversation_state = AsyncMock()
            agent.context_manager.save_classification_result = AsyncMock()
            
            agent.classification_service = MockClassificationService.return_value
            agent.classification_service.classify = AsyncMock()
            
            agent.task_planner = MockTaskPlanner.return_value
            agent.task_planner.plan_tasks = AsyncMock()
            agent.task_planner.handle_next_task = AsyncMock()
            
            agent.beneficiary_handler = MockBeneficiaryHandler.return_value
            agent.beneficiary_handler.handle_beneficiary = AsyncMock()
            
            agent.cancellation_handler = MockCancellationHandler.return_value
            agent.cancellation_handler.handle_cancellation = AsyncMock()
            
            agent.intent_router = MockIntentRouter.return_value
            agent.intent_router.route_intent = AsyncMock()
            
            # Default context manager behavior
            agent.context_manager.load_user_context.return_value = {
                "conversation_state": None,
                "last_response": None,
                "suggestion_data": None
            }
            
            # Mock transfer_service.graph for fresh start handler
            agent.transfer_service.graph = AsyncMock()
            agent.transfer_service.graph.clear_checkpoint = AsyncMock()
            
            return agent

    async def test_fresh_start_flow(self, orchestrator):
        """Test that fresh start messages clear state."""
        # Setup
        orchestrator.task_queue_service.has_active_queue.return_value = False
        orchestrator.context_manager.load_user_context.return_value = {
            "conversation_state": {"some": "state"},
            "last_response": "something",
            "suggestion_data": None
        }
        
        orchestrator.classification_service.classify.return_value = ClassificationResult(
            intent="greeting", 
            confidence=1.0,
            is_complex=False,
            complexity_reason="simple greeting",
            response="Hello"
        )
        
        orchestrator.intent_router.route_intent.return_value = "Hello there!"
        
        # Execute
        response = await orchestrator.invoke("1234567890", "Hello", "msg_123")
        
        # Verify
        orchestrator.context_manager.clear_conversation_state.assert_called_once_with("1234567890")
        orchestrator.transfer_service.graph.clear_checkpoint.assert_called_once_with("1234567890")
        assert response == "Hello there!"

    async def test_transfer_flow(self, orchestrator):
        """Test routing to transfer service."""
        # Setup
        orchestrator.classification_service.classify.return_value = ClassificationResult(
            intent="transfer", 
            confidence=0.95,
            is_complex=False,
            complexity_reason="simple transfer",
            response=""
        )
        orchestrator.intent_router.route_intent.return_value = "Transfer response"
        
        # Execute
        response = await orchestrator.invoke("1234567890", "Send money", "msg_123")
        
        # Verify
        orchestrator.intent_router.route_intent.assert_called_once()
        assert response == "Transfer response"

    async def test_cancellation_flow(self, orchestrator):
        """Test cancellation handling."""
        # Setup
        orchestrator.classification_service.classify.return_value = ClassificationResult(
            intent="cancel", 
            is_cancellation=True, 
            confidence=1.0,
            is_complex=False,
            complexity_reason="simple cancellation",
            response="Cancelled"
        )
        orchestrator.cancellation_handler.handle_cancellation.return_value = "Cancelled successfully"
        
        # Execute
        response = await orchestrator.invoke("1234567890", "Cancel", "msg_123")
        
        # Verify
        orchestrator.cancellation_handler.handle_cancellation.assert_called_once()
        assert response == "Cancelled successfully"

    async def test_active_queue_routing(self, orchestrator):
        """Test routing to active task in queue."""
        # Setup
        orchestrator.task_queue_service.has_active_queue.return_value = True
        orchestrator.task_queue_service.get_current_task.return_value = "task_1"
        orchestrator.task_queue_service.get_task_results.return_value = {}
        
        task = PlannedTask(id="task_1", action="transfer", instruction="Send 100", executor="transfer", parameters={"amount": 100})
        planner_output = PlannerOutput(
            primary_intent="transfer",
            normalized_instruction="Send 100",
            tasks=[task]
        )
        orchestrator.task_queue_service.get_task_queue.return_value = planner_output
        
        orchestrator.transfer_service.run_simple.return_value = "Transfer processing"
        
        # Execute
        response = await orchestrator.invoke("1234567890", "My account is 123", "msg_123")
        
        # Verify
        orchestrator.transfer_service.run_simple.assert_called_once()
        # Check that task parameters were passed correctly
        call_args = orchestrator.transfer_service.run_simple.call_args
        assert call_args[0][0] == "1234567890"
        assert call_args[0][1] == "My account is 123"
        assert call_args[0][2] == {"intent": "transfer", "task_parameters": {"amount": 100}}
        assert response == "Transfer processing"

    async def test_batch_authorization(self, orchestrator):
        """Test batch authorization trigger."""
        # Setup
        orchestrator.task_queue_service.has_active_queue.return_value = True
        orchestrator.task_queue_service.get_task_results.return_value = {
            "task_1": {"status": "collection_complete"}
        }
        
        task = PlannedTask(id="task_1", action="transfer", instruction="Send 100", executor="transfer")
        planner_output = PlannerOutput(
            primary_intent="transfer",
            normalized_instruction="Send 100",
            tasks=[task]
        )
        orchestrator.task_queue_service.get_task_queue.return_value = planner_output
        
        orchestrator.classification_service.classify.return_value = ClassificationResult(
            intent="confirm", 
            confidence=1.0,
            is_complex=False,
            complexity_reason="simple confirmation",
            response=""
        )
        
        orchestrator.transfer_service.run_simple.return_value = "Auth flow sent"
        
        # Execute
        response = await orchestrator.invoke("1234567890", "Yes, proceed", "msg_123")
        
        # Verify
        orchestrator.task_queue_service.set_current_task.assert_called_with("1234567890", "task_1")
        orchestrator.transfer_service.run_simple.assert_called_with(
            "1234567890", "authorize", {"intent": "transfer"}
        )
        assert response == "Auth flow sent"
