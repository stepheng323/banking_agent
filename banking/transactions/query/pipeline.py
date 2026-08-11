"""Abstract contract for query worker steps."""

from abc import ABC, abstractmethod
from typing import Any

from banking.runtime.results import TransactionResult


class QueryStep(ABC):
    """Abstract base class for a single step in the query pipeline."""

    @abstractmethod
    async def run(
        self,
        state: dict[str, Any],
        worker_context: Any = None,
    ) -> TransactionResult:
        """Execute the step logic."""
        pass
