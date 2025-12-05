"""Flow completion callback interface for multi-task execution."""

from typing import Protocol, Any


class FlowCompletionCallback(Protocol):
    """Protocol for flow completion callbacks."""

    async def on_flow_complete(
        self,
        phone_number: str,
        flow_type: str,
        result: dict[str, Any],
    ) -> None:
        """
        Called when a flow completes.

        Args:
            phone_number: User's phone number
            flow_type: Type of flow that completed (transfer, airtime, etc.)
            result: Flow execution result with status and any relevant data
        """
        ...

