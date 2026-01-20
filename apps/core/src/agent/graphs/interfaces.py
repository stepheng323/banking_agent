"""Interface for transaction services (transfer, airtime, etc)."""

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FlowCompletionCallback(Protocol):
    """Protocol for flow completion callbacks.

    This protocol allows services to depend on an abstraction rather than
    a concrete class, avoiding circular dependencies.
    """

    async def on_flow_complete(self, phone_number: str, flow_type: str, result: dict[str, Any]) -> None:
        """Called when a flow completes.

        Args:
            phone_number: User's phone number
            flow_type: Type of flow (transfer, airtime, data, etc.)
            result: Flow completion result dictionary
        """
        ...


class IAgentService(ABC):
    """Common interface for all agent services.

    This interface enforces encapsulation by requiring services to expose
    only their public API, hiding internal graph implementations.
    """

    @abstractmethod
    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the transaction flow."""
        pass

    @abstractmethod
    async def preflight(
        self,
        phone: str,
        text: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Validate and enrich parameters before execution.

        Returns:
            dict containing:
            - ready: bool
            - missing_fields: list[str]
            - enriched_params: dict[str, Any]
            - question: str | None
        """
        pass

    @abstractmethod
    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear flow checkpoint for a user.

        This method must be implemented by all services to ensure
        checkpoint management is part of the service interface, not
        accessed through internal graph implementations.

        Args:
            phone_number: User's phone number
        """
        pass

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, extra_param: Any = None
    ) -> str:
        """Resume flow after PIN verification (optional).

        Only services that require PIN verification (transfer, airtime, data)
        need to implement this method. Default implementation raises
        NotImplementedError to prevent accidental usage.

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            extra_param: Service-specific parameter
                - For transfer/airtime: pin_error (str | None)
                - For data: user_id (str | None)

        Returns:
            Response message after resuming flow

        Raises:
            NotImplementedError: If service doesn't support PIN verification
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support PIN verification")

    def set_completion_callback(self, callback: FlowCompletionCallback | None) -> None:
        """Set the completion callback for the flow (optional).

        Only services that need completion callbacks (transfer, airtime)
        need to implement this method. Default implementation is a no-op.

        Args:
            callback: Completion callback implementing FlowCompletionCallback protocol
        """
        # Default implementation: no-op for services that don't need it
        pass
