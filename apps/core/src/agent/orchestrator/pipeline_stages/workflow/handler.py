"""Workflow pipeline handler.

Integrates WorkflowOrchestrator into the orchestrator pipeline.
Handles multi-task execution via DAG engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import redis

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.workflow import (
    WorkflowOrchestrator,
    WorkflowStatus,
    WorkflowPersistence,
    create_workflow_executor,
)
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.protocols.services import (
    TransferServiceProtocol,
    AirtimeServiceProtocol,
    DataServiceProtocol,
    QueryServiceProtocol,
    AccountManagementServiceProtocol,
    SupportServiceProtocol,
    FAQServiceProtocol,
)
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import OrchestratorTaskPlanner
    from shared.cache.user_data import UserDataCache

logger = get_logger(__name__)


class WorkflowHandler(MessageHandler):
    """
    Pipeline handler for workflow execution.
    
    Replaces TaskQueueHandler with DAG-based execution.
    """
    
    def __init__(
        self,
        task_planner: OrchestratorTaskPlanner,
        transfer_service: TransferServiceProtocol,
        airtime_service: AirtimeServiceProtocol,
        query_service: QueryServiceProtocol,
        data_service: DataServiceProtocol | None,
        account_management_service: AccountManagementServiceProtocol,
        support_service: SupportServiceProtocol,
        faq_service: FAQServiceProtocol,
        user_cache: UserDataCache | None = None,
        redis_client: redis.Redis | None = None,
        whatsapp_client: WhatsAppClient | None = None,
        mode: Literal["planning", "execution", "both"] = "both",
    ) -> None:
        super().__init__()
        self.task_planner = task_planner
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.mode = mode
        
        executor = create_workflow_executor(
            transfer_service=transfer_service,
            query_service=query_service,
            airtime_service=airtime_service,
            data_service=data_service,
            account_management_service=account_management_service,
            support_service=support_service,
            faq_service=faq_service,
        )

        
        persistence = None
        if redis_client:
            persistence = WorkflowPersistence(redis_client)
        
        self.workflow_orchestrator = WorkflowOrchestrator(
            executor=executor,
            persistence=persistence,
            user_cache=user_cache,
        )
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        1. There's a planner output with tasks
        2. There's an active workflow to resume
        3. Not yet processed (will invoke planner)
        
        We always try the planner first - it handles classification + planning.
        Only skip to IntentRouting if planner returns no tasks (conversational).
        """
        # Already has tasks
        if context.planner_output and context.planner_output.tasks:
            return True
        
        # Resume active workflow
        if context.phone_number and hasattr(self.workflow_orchestrator, 'persistence'):
            persistence = self.workflow_orchestrator.persistence
            if persistence:
                has_active = await persistence.has_active_workflow(context.phone_number)
                if has_active:
                    return True
        
        # Always try planner for fresh requests (planner handles classification)
        if not context.handled:
            return True
        
        return False

    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Execute or resume workflow based on mode."""
        phone_number = context.phone_number
        pin_verified = getattr(context, "pin_verified", False)
        
        # --- PLANNING PHASE ---
        if self.mode in ["planning", "both"]:
            # Check for existing tasks
            if not (context.planner_output and context.planner_output.tasks):
                
                # --- SESSION GATE CHECK ---
                persistence = self.workflow_orchestrator.persistence
                active_metadata = None
                if persistence:
                    active_metadata = await persistence.get_active_workflow_metadata(phone_number)
                
                gate_decision = "ROUTE"
                if active_metadata:
                    gate_decision = await self._run_session_gate(context.text, active_metadata)
                
                # Execute Logic based on Gate Decision
                if gate_decision == "RESUME":
                    # Resume existing flow
                    result = await self.workflow_orchestrator.resume_workflow(
                        phone_number=phone_number,
                        additional_input={"user_text": context.text},
                        pin_verified=pin_verified,
                    )
                    
                    if result:
                        # Process response immediately
                        await self.respond(context, result.user_message)
                        context.handled = True
                        return context
                
                elif gate_decision == "CANCEL":
                    # Cancel active workflow
                    await self.workflow_orchestrator.persistence.clear(phone_number, active_metadata.workflow_id)
                    await self.respond(context, "Cancelled.")
                    context.handled = True
                    return context
                
                elif gate_decision == "BLOCK":
                    # Reject request
                    await self.respond(context, "Please complete the current transaction first.")
                    context.handled = True
                    return context
                    
                elif gate_decision == "CONFIRM":
                    # TODO: Implement Abandon Confirmation logic (store simple intent state?)
                    # For now, we block, or resume as text?
                    # User recommended: Ask "Abandon transfer?"
                    # We can use affirmation handler?
                    # Simplifying for this increment: Treat as Resume (graph decides) or Block?
                    # Let's BLOCK for cleanliness for now, or route to planner if we want smart handling?
                    # Actually, CONFIRM usually means we pause. 
                    # Let's try ROUTE to planner, but planner might overwrite?
                    # Let's BLOCK with specific message.
                    await self.respond(context, "Do you want to abandon the current task? (Say 'cancel' to stop)")
                    context.handled = True
                    return context

                # If ROUTE or no active session, proceed to Planner
                if not context.handled:
                    # Build context string for planner
                    context_str = "None"
                    if hasattr(self.workflow_orchestrator, 'persistence'):
                         persistence = self.workflow_orchestrator.persistence
                         if persistence and active_metadata:
                              # Provide simple context for planner about background task
                              context_str = f"Active Flow: {active_metadata.graph_name} (Status: {active_metadata.status})"
                    
                    logger.info("planner_context_debug", phone=phone_number, context=context_str)

                    # Invoke planner (handles classification + planning in one call)
                    planner_output = await self.task_planner.plan_tasks(phone_number, context.text, context=context_str)
                    
                    if planner_output:
                        logger.info(
                            "planner_result",
                            intent=planner_output.primary_intent,
                            task_count=len(planner_output.tasks),
                            confidence=planner_output.confidence,
                        )
                        
                        # Populate context with planner output
                        context = context.update(planner_output=planner_output)
                        
                        # COMPATIBILITY: Populate classification_result for Guardian/FlowControl
                        from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
                        
                        classification = ClassificationResult(
                            response=planner_output.response,
                            intent=planner_output.primary_intent,
                            is_complex=planner_output.is_complex,
                            complexity_reason="Planned by workflow",
                            confidence=planner_output.confidence,
                            is_cancellation=planner_output.is_cancellation,
                            detected_language=planner_output.detected_language,
                            conversation_state=None
                        )
                        context = context.update(classification_result=classification)
                        
                        if not planner_output.tasks:
                             # Conversational - return immediately (no point sending to execution)
                             response = planner_output.response or "How can I help you today?"
                             return context.with_response(response, handled=True)


        # --- EXECUTION PHASE ---
        if self.mode in ["execution", "both"]:
            # If we just planned and have no tasks (conversational), return response
            if context.planner_output and not context.planner_output.tasks and not context.handled:
                 response = context.planner_output.response or "How can I help you today?"
                 return context.with_response(response, handled=True)

            # Skip execution if no planner output (nothing to execute)
            if not context.planner_output or not context.planner_output.tasks:
                # If there's a classification result, let IntentRouting handle it
                # Otherwise, return a default response
                if not context.classification_result:
                    return context.with_response("How can I help you today?", handled=True)
                return context

            # Execute workflow with tasks
            result = await self.workflow_orchestrator.execute_workflow(
                phone_number=phone_number,
                planner_output=context.planner_output,
                pin_verified=pin_verified,
            )
            
            return await self._build_response(context, result)
            
        return context



    
    async def _run_session_gate(
        self,
        message: str,
        metadata: Any, # ActiveWorkflowMetadata
    ) -> str:
        """Decide how to handle message given active workflow."""
        from apps.core.src.agent.workflow.inputs import matches_expected_input, is_cancellation
        from apps.core.src.agent.workflow.models import InterruptPolicy
        
        # 1. Check Cancellation
        if is_cancellation(message):
            return "CANCEL"
            
        # 2. Check Expected Input (Match)
        if matches_expected_input(message, metadata.expected_input):
            return "RESUME"
            
        # 3. Check Policy for Routing/Breakout
        if metadata.interrupt_policy == InterruptPolicy.ALLOW:
            return "ROUTE"
            
        elif metadata.interrupt_policy == InterruptPolicy.BLOCK:
            return "BLOCK"
            
        elif metadata.interrupt_policy == InterruptPolicy.CONFIRM:
             return "CONFIRM"
             
        return "ROUTE"

    async def _build_response(self, context: MessageContext, result: Any) -> MessageContext:
        """Build response context from workflow result."""
        if result.status == WorkflowStatus.COMPLETED:
            responses = []
            for task_result in result.task_results.values():
                if task_result.data and task_result.data.get("response"):
                    responses.append(task_result.data["response"])
            
            if responses:
                response = "\n\n".join(responses)
            else:
                response = result.user_message or "Done!"
            
            return context.with_response(response, handled=True)
        
        elif result.status == WorkflowStatus.PARTIAL_SUCCESS:
            return context.with_response(
                result.user_message or "Some tasks completed, others failed.",
                handled=True,
            )
        
        elif result.status == WorkflowStatus.WAITING_FOR_INPUT:
            return context.with_response(
                result.user_message or "I need more information.",
                handled=True,
            )
        
        elif result.status == WorkflowStatus.AWAITING_AUTH:
            # Centralized Send Logic (Headless Adapter)
            if not self.whatsapp_client:
                logger.error("whatsapp_client_missing_for_auth")
                return context.with_response("System error: Messaging unavailable.", handled=True)

            # 1. Aggregate Summaries
            summaries = []
            tokens = []
            # result.pending_auth_tasks contains the IDs of tasks waiting for auth
            auth_task_ids = getattr(result, "pending_auth_tasks", []) or []
            
            for tid in auth_task_ids:
                t_res = result.task_results.get(tid)
                if t_res and t_res.data:
                    # Try to get confirmation summary
                    summary = t_res.data.get("confirmation_summary")
                    # Fallback to response if summary missing (legacy compat)
                    if not summary:
                        summary = t_res.data.get("response")
                    
                    if summary:
                        summaries.append(summary)
                    
                    if "confirmation_token" in t_res.data:
                        tokens.append(t_res.data["confirmation_token"])

            combined_summary = "\n\n".join(summaries)
            if not combined_summary:
                combined_summary = "Please authorize this transaction."

            # 2. Determine Token 
            # We use the first available token from the graphs.
            # Since graphs already registered these tokens in Redis, using one is sufficient
            # to trigger the PIN flow. Upon verification, 'pin_verified=True' will unlock ALL tasks.
            import uuid
            target_token = tokens[0] if tokens else f"auth-{uuid.uuid4()}-{context.phone_number}"

            logger.info("orchestrator_sending_auth_flow", phone=context.phone_number, token=target_token)

            # 3. Send Flow
            await self.whatsapp_client.send_flow(
                to=context.phone_number,
                header="Authorize Request",
                flow_cta="Authorize",
                flow_id=settings.pin_confirmation_flow_id,
                screen_name="Pin",
                flow_token=target_token,
                text_body=combined_summary,
                message_id=context.message_id
            )

            return context.with_response("", handled=True)
        
        elif result.status == WorkflowStatus.FAILED:
            return context.with_response(
                result.user_message or "Something went wrong.",
                handled=True,
            )
        
        return context
