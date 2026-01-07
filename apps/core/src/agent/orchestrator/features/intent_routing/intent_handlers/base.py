"""Base class for intent handlers."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext


class IntentHandler(ABC):
    """Abstract base class for intent handlers.

    Each intent type (transfer, airtime, query, etc.) has its own handler
    that implements this interface.
    """

    @abstractmethod
    def can_handle(self, intent: str) -> bool:
        """Return True if this handler handles the given intent.

        Args:
            intent: Lowercase intent string from classification

        Returns:
            True if this handler should handle the intent
        """
        pass

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        """Flows that should be paused when switching to this intent.

        Override in subclass to specify which active flows should be
        paused before handling this intent.

        Returns:
            Tuple of flow type strings (e.g., ("transfer", "airtime"))
        """
        return ()

    @property
    def supports_resume_prompt(self) -> bool:
        """Whether to append a resume prompt after handling.

        Override to return True for intents that interrupt transactional
        flows (query, data, manage_accounts).

        Returns:
            True to append resume prompt after response
        """
        return False

    @property
    def send_ack_before_handling(self) -> bool:
        """Whether to send acknowledgment message before handling.

        Override to return False for handlers that manage their own acks.

        Returns:
            True to send result.response as ack before handling
        """
        return True

    @abstractmethod
    async def handle(self, ctx: "RoutingContext") -> str:
        """Handle the intent and return response.

        Args:
            ctx: Routing context with all request data

        Returns:
            Response string to send to user
        """
        pass
