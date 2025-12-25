"""Interface for transaction services (transfer, airtime, etc)."""

from abc import ABC, abstractmethod
from typing import Optional


class ITransactionService(ABC):
    """Common interface for all transaction services."""
    
    @abstractmethod
    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: Optional[dict] = None,
        image_data: str | None = None
    ) -> str:
        """Run the transaction flow."""
        pass
