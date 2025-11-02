"""Abstract base class for payment service providers."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class PaymentProvider(ABC):
    """
    Abstract interface for payment service providers.

    Payment providers typically offer multiple services:
    - Account resolution/verification
    - Payment processing
    - Transfer execution
    - Bill payments
    - etc.
    """

    @abstractmethod
    async def resolve_account(
        self, account_number: str, bank_code: str, currency: str = "NGN"
    ) -> Dict[str, Any]:
        """
        Resolve bank account details using the provider's API.

        Args:
            account_number: The bank account number to verify
            bank_code: The bank code (e.g., "058" for GTBank, "011" for First Bank)
            currency: Currency code (default: "NGN")

        Returns:
            Dictionary with:
                - success: bool
                - account_name: str (account holder name) if successful
                - account_number: str (normalized account number)
                - bank_code: str
                - error: str if failed
                - provider: str (provider name, e.g., "flutterwave")

        Raises:
            ValueError: If credentials are not configured
        """
        raise NotImplementedError

    @abstractmethod
    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: Optional[str] = None,
        narration: Optional[str] = None,
        currency: str = "NGN",
    ) -> Dict[str, Any]:
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
        raise NotImplementedError(
            f"{self.provider_name} does not support transfers yet")

    @abstractmethod
    async def get_transfer_status(
        self, transaction_id: str
    ) -> Dict[str, Any]:
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
        raise NotImplementedError(
            f"{self.provider_name} does not support status checks yet")

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
