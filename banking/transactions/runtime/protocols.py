"""Executor Protocols.

Defines the expected interface for Transaction Executors used by the TransactionConsumer.
These protocols allow type safety without depending on concrete (and potentially missing) executor classes.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TransferExecutorProtocol(Protocol):
    """Protocol for Transfer Executor."""

    async def handle_transfer(self, data: dict[str, Any]) -> None:
        """Handle execution of a transfer transaction."""
        ...


@runtime_checkable
class AirtimeExecutorProtocol(Protocol):
    """Protocol for Airtime Executor."""

    async def handle_airtime(self, data: dict[str, Any]) -> None:
        """Handle execution of an airtime transaction."""
        ...


@runtime_checkable
class DataExecutorProtocol(Protocol):
    """Protocol for Data Executor."""

    async def handle_data(self, data: dict[str, Any]) -> None:
        """Handle execution of a data transaction."""
        ...
