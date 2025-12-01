"""Classification handler - classifies user intent."""

import asyncio
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.classification_service import OrchestratorClassificationService
from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager


class ClassificationHandler(MessageHandler):
    """
    Classifies user intent using LLM.
    
    Runs after context loading to classify the message.
    """
    
    def __init__(
        self,
        classification_service: OrchestratorClassificationService,
        context_manager: OrchestratorContextManager,
    ):
        self.classification_service = classification_service
        self.context_manager = context_manager
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Always runs to classify intent."""
        return context.classification_result is None
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Classify user intent."""
        # Build classification context
        classification_context = {}
        if context.conversation_state:
            classification_context["conversationState"] = context.conversation_state
        
        if context.suggestion_context:
            classification_context["suggestionContext"] = context.suggestion_context
        
        # Classify intent
        result = await self.classification_service.classify(
            context.text,
            classification_context,
            context.last_response
        )
        
        # Save classification result asynchronously
        asyncio.create_task(
            self.context_manager.save_classification_result(context.phone_number, result)
        )
        
        # Return updated context
        return context.update(classification_result=result)
