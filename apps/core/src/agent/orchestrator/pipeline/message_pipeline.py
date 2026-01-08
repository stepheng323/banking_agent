"""Message processing pipeline."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MessagePipeline:
    """
    Processes messages through a chain of handlers.

    Each handler is checked in order. If a handler can handle the message,
    it processes it and may set handled=True to stop further processing.
    """

    def __init__(self, handlers: list[MessageHandler]):
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
                logger.info(f"🔄 [PIPELINE] processing_message with handler: {handler.__class__.__name__}")

                current_context = await handler.handle(current_context)

                if current_context.handled:
                    logger.info(f"✅ [PIPELINE] message_handled by {handler.__class__.__name__}")
                    break

            else:
                logger.debug(f"⏭️ [PIPELINE] Skipping handler {handler.__class__.__name__} (cannot handle)")

        return (
            current_context.response if current_context.response is not None else "I'm not sure how to help with that."
        )
