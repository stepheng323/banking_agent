"""Message processing pipeline."""

from typing import List
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler


class MessagePipeline:
    """
    Processes messages through a chain of handlers.
    
    Each handler is checked in order. If a handler can handle the message,
    it processes it and may set handled=True to stop further processing.
    """
    
    def __init__(self, handlers: List[MessageHandler]):
        """
        Initialize pipeline with handlers.
        
        Args:
            handlers: List of handlers in execution order
        """
        self.handlers = handlers
    
    async def process(self, context: MessageContext) -> str:
        """
        Process message through the pipeline.
        
        Args:
            context: Initial message context
            
        Returns:
            Response message
        """
        current_context = context
        
        for handler in self.handlers:
            if await handler.can_handle(current_context):
                print(f"🔄 [{handler.name}] Processing message")
                
                current_context = await handler.handle(current_context)
                
                if current_context.handled:
                    print(f"✅ [{handler.name}] Message handled")
                    break
        
        return current_context.response or "I'm not sure how to help with that."
