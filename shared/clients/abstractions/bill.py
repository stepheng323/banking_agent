"""Abstract base class for bill payment service providers."""

from abc import ABC, abstractmethod
from typing import Any


class BillPaymentProvider(ABC):
    """
    Abstract interface for bill payment service providers.

    Handles airtime, data, cable TV, electricity, and other bill payments.
    Separated from PayoutProvider to allow independent provider selection.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of the provider (e.g., 'flutterwave', 'vtpass')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is properly configured and available."""
        raise NotImplementedError

    @property
    def supports_airtime(self) -> bool:
        """Check if the provider supports airtime purchases."""
        return False

    @property
    def supports_data(self) -> bool:
        """Check if the provider supports data purchases."""
        return False

    @abstractmethod
    async def purchase_airtime(
        self,
        amount: float,
        recipient_phone: str,
        network: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """
        Purchase airtime for a phone number.

        Args:
            amount: Amount of airtime to purchase
            recipient_phone: Phone number to top up
            network: Network provider (MTN, AIRTEL, GLO, 9MOBILE)
            reference: Optional unique transaction reference

        Returns:
            Dictionary with:
                - success: bool
                - transaction_id: str (if successful)
                - message: str
                - amount: float
                - recipient_phone: str
                - network: str
                - error: str (if failed)
                - provider: str
        """
        raise NotImplementedError

    async def fetch_bill_categories(self, category: str = "AIRTIME") -> dict[str, Any]:
        """
        Fetch available bill categories/billers from the provider.

        Args:
            category: Category to filter (e.g., "AIRTIME", "MOBILEDATA", "CABLE")

        Returns:
            Dictionary with billers list and their codes
        """
        raise NotImplementedError(f"{self.provider_name} does not support fetching bill categories")

    async def get_data_plans(self, network: str) -> dict[str, Any]:
        """
        Get available data plans for a network.

        Args:
            network: Network provider (MTN, AIRTEL, GLO, 9MOBILE)

        Returns:
            Dictionary with:
                - success: bool
                - plans: list of dicts with item_code, name, amount, validity
                - error: str (if failed)
        """
        raise NotImplementedError(f"{self.provider_name} does not support fetching data plans")

    async def purchase_data(
        self,
        plan_code: str,
        recipient_phone: str,
        network: str,
        amount: float | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """
        Purchase a data plan for a phone number.

        Args:
            plan_code: The item_code of the data plan
            recipient_phone: Phone number to credit
            network: Network provider (MTN, AIRTEL, GLO, 9MOBILE)
            amount: Exact catalog amount for the selected plan
            reference: Optional unique transaction reference

        Returns:
            Dictionary with:
                - success: bool
                - transaction_id: str (if successful)
                - message: str
                - plan_code: str
                - recipient_phone: str
                - network: str
                - error: str (if failed)
                - provider: str
        """
        raise NotImplementedError(f"{self.provider_name} does not support data purchases")
