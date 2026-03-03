"""Abstract base class for payout service providers."""

from abc import ABC, abstractmethod
from typing import Any


class PayoutProvider(ABC):
    """
    Abstract interface for payout execution providers.
    """

    @abstractmethod
    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: str | None = None,
        narration: str | None = None,
        currency: str = "NGN",
    ) -> dict[str, Any]:
        """
        Initiate a bank transfer.

        Args:
            amount: Transfer amount
            recipient_account_number: Recipient's account number
            recipient_bank_code: Recipient's bank code
            sender_account_number: Optional sender account number
            narration: Optional transfer narration/description
            currency: Currency code (default: "NGN")

        Returns:
            Dictionary with:
                - success: bool
                - transaction_id: str (if successful)
                - status: str (e.g., "pending", "success", "failed")
                - error: str (if failed)
                - provider: str

        Raises:
            NotImplementedError: If provider doesn't support transfers yet
        """
        raise NotImplementedError(f"{self.provider_name} does not support transfers yet")

    @abstractmethod
    async def get_transfer_status(self, transaction_id: str) -> dict[str, Any]:
        """
        Get the status of a transfer transaction.

        Args:
            transaction_id: The transaction ID from initiate_transfer

        Returns:
            Dictionary with:
                - success: bool
                - status: str (e.g., "pending", "success", "failed")
                - transaction_id: str
                - error: str (if failed)
                - provider: str

        Raises:
            NotImplementedError: If provider doesn't support status checks yet
        """
        raise NotImplementedError(f"{self.provider_name} does not support status checks yet")

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of the provider (e.g., 'flutterwave', 'paystack')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is properly configured and available."""
        raise NotImplementedError

    @property
    def supports_transfers(self) -> bool:
        """Check if the provider supports transfer operations."""
        return False

    @property
    def supports_status_checks(self) -> bool:
        """Check if the provider supports transaction status checks."""
        return False
