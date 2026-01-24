"""Worker Protocol.

Defines the interface for all V3 Domain Workers invoked by the Orchestrator.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class WorkerProtocol(Protocol):
    """Protocol for stateless domain workers."""

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> BaseModel:
        """Type safe run method.

        Returns:
            A Pydantic model (usually TransactionResult or AccountResult).
        """
        ...
