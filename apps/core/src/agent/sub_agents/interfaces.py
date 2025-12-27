"""Interface for transaction services (transfer, airtime, etc)."""

from abc import ABC, abstractmethod


class ITransactionService(ABC):
    """Common interface for all transaction services."""

    @abstractmethod
    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the transaction flow.

        Args:
            phone: User's phone number
            text: User's message
            classification_result: Classification result from orchestrator
            image_data: Optional base64 image data
            quoted_data: Data from quoted transaction (for repeat/modify)
        """
        pass
