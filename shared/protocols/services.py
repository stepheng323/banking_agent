"""Protocol definitions for agent services.

Services implement these protocols. Handlers depend on protocols, not concrete classes.
This breaks circular imports by introducing an abstraction layer.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.subgraph_types import (
        TransferSubgraphInput,
        TransferSubgraphResult,
    )


@runtime_checkable
class TransferServiceProtocol(Protocol):
    """Protocol for transfer service operations."""

    async def run_simple(self, phone: str, text: str, classification_result: dict[str, Any]) -> str:
        """Execute a simple transfer request."""
        ...

    async def invoke_task(
        self,
        input: "TransferSubgraphInput",
    ) -> "TransferSubgraphResult":
        """Invoke transfer subgraph as a pure worker.

        Modes:
        - resolve: Load context, resolve references
        - validate: Validate data, compute confirmation
        - execute: Execute the transfer
        """
        ...

    async def clear_checkpoint(self, phone: str) -> None:
        """Clear transfer checkpoint for a user."""
        ...

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """Get the last workflow state (checkpoint)."""
        ...

    async def preflight(self, phone: str, text: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and enrich parameters."""
        ...


@runtime_checkable
class AirtimeServiceProtocol(Protocol):
    """Protocol for airtime service operations."""

    async def run_simple(self, phone: str, text: str, classification_result: dict[str, Any]) -> str:
        """Execute a simple airtime request."""
        ...

    async def clear_checkpoint(self, phone: str) -> None:
        """Clear airtime checkpoint for a user."""
        ...

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """Get the last workflow state (checkpoint)."""
        ...

    async def preflight(self, phone: str, text: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and enrich parameters."""
        ...


@runtime_checkable
class DataServiceProtocol(Protocol):
    """Protocol for data purchase service operations."""

    async def run_simple(self, phone: str, text: str, classification_result: dict[str, Any]) -> str:
        """Execute a simple data purchase request."""
        ...

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """Get the last workflow state (checkpoint)."""
        ...

    async def preflight(self, phone: str, text: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and enrich parameters."""
        ...


@runtime_checkable
class QueryServiceProtocol(Protocol):
    """Protocol for account query operations."""

    async def handle_query(self, phone: str, text: str) -> str:
        """Handle an account query request."""
        ...


@runtime_checkable
class AccountManagementServiceProtocol(Protocol):
    """Protocol for account management operations."""

    async def handle(self, phone: str, text: str) -> str:
        """Handle an account management request."""
        ...


@runtime_checkable
class SupportServiceProtocol(Protocol):
    """Protocol for support/help operations."""

    async def handle(self, phone: str, text: str) -> str:
        """Handle a support request."""
        ...


@runtime_checkable
class FAQServiceProtocol(Protocol):
    """Protocol for FAQ operations."""

    async def handle(self, phone: str, text: str) -> str:
        """Handle a FAQ request."""
        ...
