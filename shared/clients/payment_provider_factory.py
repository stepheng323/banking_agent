"""Factory for creating and managing payment and bill payment providers."""
from typing import List, Optional

from shared.clients.payment_provider import PaymentProvider
from shared.clients.bill_payment_provider import BillPaymentProvider
from shared.clients.flutterwave_client import FlutterwaveClient
from shared.clients.flutterwave_bills_client import FlutterwaveBillsClient


class PaymentProviderFactory:
    """Factory for managing payment and bill payment providers."""

    DEFAULT_PROVIDER_ORDER = ["flutterwave"]

    @staticmethod
    def create_provider(provider_name: str) -> Optional[PaymentProvider]:
        """Create a payment provider instance by name."""
        if provider_name == "flutterwave":
            try:
                return FlutterwaveClient()
            except ValueError:
                return None
        return None

    @staticmethod
    def create_bill_payment_provider(provider_name: str) -> Optional[BillPaymentProvider]:
        """Create a bill payment provider instance by name."""
        if provider_name == "flutterwave":
            try:
                return FlutterwaveBillsClient()
            except ValueError:
                return None
        return None

    @staticmethod
    def get_available_providers(
        priority_order: Optional[List[str]] = None
    ) -> List[PaymentProvider]:
        """Get all available payment providers in priority order."""
        if priority_order is None:
            priority_order = PaymentProviderFactory.DEFAULT_PROVIDER_ORDER

        providers = []
        for provider_name in priority_order:
            provider = PaymentProviderFactory.create_provider(provider_name)
            if provider and provider.is_available:
                providers.append(provider)
        return providers

    @staticmethod
    def get_primary_provider() -> Optional[PaymentProvider]:
        """Get the primary (highest priority) available payment provider."""
        providers = PaymentProviderFactory.get_available_providers()
        return providers[0] if providers else None

    @staticmethod
    def get_provider_by_name(provider_name: str) -> Optional[PaymentProvider]:
        """Get a specific payment provider by name."""
        provider = PaymentProviderFactory.create_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_bill_payment_provider(provider_name: str = "flutterwave") -> Optional[BillPaymentProvider]:
        """
        Get a bill payment provider for airtime, data, and utility payments.
        
        Args:
            provider_name: Name of the provider (default: "flutterwave")
            
        Returns:
            Bill payment provider instance if available, None otherwise
        """
        provider = PaymentProviderFactory.create_bill_payment_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_provider_for_service(service: str) -> Optional[PaymentProvider]:
        """Get the best available payment provider for a specific service."""
        providers = PaymentProviderFactory.get_available_providers()

        if service == "resolve_account":
            return providers[0] if providers else None

        elif service == "initiate_transfer":
            for provider in providers:
                if provider.supports_transfers:
                    return provider
            return None

        elif service == "get_transfer_status":
            for provider in providers:
                if provider.supports_status_checks:
                    return provider
            return None

        return None

