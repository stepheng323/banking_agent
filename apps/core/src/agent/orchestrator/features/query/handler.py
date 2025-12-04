"""Query handler for the orchestrator pipeline."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.query.service import QueryService


class QueryHandler(MessageHandler):
    """
    Handles financial query requests.
    
    This handler processes natural language questions about user transactions.
    """
    
    def __init__(self, query_service: QueryService):
        """
        Initialize query handler.
        
        Args:
            query_service: Service for answering financial questions
        """
        self.query_service = query_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Check if this is a query intent."""
        return context.classification_result is not None and context.classification_result.intent == "query"
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Handle query request.
        
        Args:
            context: Message context
            
        Returns:
            Updated context with response
        """
        try:
            user_profile = context.user_context.get("profile", {})
            mono_account_id = user_profile.get("mono_account_id")
            
            if not mono_account_id:
                return context.update(
                    response="I need access to your bank account to answer financial questions. Please link your account first.",
                    handled=True
                )
            
            response = await self.query_service.answer_question(
                user_id=user_profile.get("id"),
                account_id=mono_account_id,
                question=context.text
            )
            
            return context.update(response=response, handled=True)
            
        except Exception as e:
            print(f"Error in QueryHandler: {e}")
            return context.update(
                response="I'm having trouble answering your question right now. Please try again.",
                handled=True
            )
