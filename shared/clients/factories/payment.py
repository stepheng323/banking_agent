"""Factory for creating and managing payment and bill payment providers."""

from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.abstractions.payment import PaymentProvider
from shared.clients.providers.flutterwave.bill import FlutterwaveBillsClient


class PaymentProviderFactory:
    """Factory for managing payment and bill payment providers."""

    DEFAULT_PROVIDER_ORDER = ["mono", "flutterwave"]

    @staticmethod
    def create_provider(provider_name: str) -> PaymentProvider | None:
        """Create a payment provider instance by name."""
        if provider_name == "flutterwave":
            try:
                from shared.clients.providers.flutterwave.payment import FlutterwavePaymentProvider

                return FlutterwavePaymentProvider()
            except ValueError:
                return None
        elif provider_name == "mono":
            try:
                from shared.clients.providers.mono.payment import MonoPaymentProvider

                return MonoPaymentProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def create_bill_payment_provider(provider_name: str) -> BillPaymentProvider | None:
        """Create a bill payment provider instance by name."""
        if provider_name == "flutterwave":
            try:
                return FlutterwaveBillsClient()
            except ValueError:
                return None
        return None

    @staticmethod
    def create_banking_data_provider(provider_name: str) -> BankingDataProvider | None:
        """Create a banking data provider instance by name."""
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.banking import MonoBankingProvider

                return MonoBankingProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def get_available_providers(priority_order: list[str] | None = None) -> list[PaymentProvider]:
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
    def get_primary_provider() -> PaymentProvider | None:
        """Get the primary (highest priority) available payment provider."""
        providers = PaymentProviderFactory.get_available_providers()
        return providers[0] if providers else None

    @staticmethod
    def get_provider_by_name(provider_name: str) -> PaymentProvider | None:
        """Get a specific payment provider by name."""
        provider = PaymentProviderFactory.create_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_bill_payment_provider(provider_name: str = "flutterwave") -> BillPaymentProvider:
        """
        Get a bill payment provider for airtime, data, and utility payments.

        Args:
            provider_name: Name of the provider (default: "flutterwave")

        Returns:
            Bill payment provider instance
        """
        return PaymentProviderFactory.create_bill_payment_provider(provider_name)

    @staticmethod
    def get_banking_data_provider(provider_name: str = "mono") -> BankingDataProvider | None:
        """
        Get a banking data provider for accounts, transactions, and BVN.

        Args:
            provider_name: Name of the provider (default: "mono")

        Returns:
            Banking data provider instance if available, None otherwise
        """
        provider = PaymentProviderFactory.create_banking_data_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_provider_for_service(service: str) -> PaymentProvider | None:
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
