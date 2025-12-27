"""Base class for message handlers in the pipeline."""

from abc import ABC, abstractmethod

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext


class MessageHandler(ABC):
    """
    Base class for all message handlers in the pipeline.

    Each handler implements:
    - can_handle(): Check if this handler should process the message
    - handle(): Process the message and return updated context
    """

    @abstractmethod
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Check if this handler can process the message.

        Args:
            context: Current message context

        Returns:
            True if this handler should process the message
        """
        pass

    @abstractmethod
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Process the message and return updated context.

        Args:
            context: Current message context

        Returns:
            Updated message context (may set response and handled=True)
        """
        pass

    @property
    def name(self) -> str:
        """Get handler name for logging."""
        return self.__class__.__name__
