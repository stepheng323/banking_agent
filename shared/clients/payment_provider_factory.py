"""Factory for creating and managing payment service providers."""
from typing import List, Optional

from shared.clients.payment_provider import PaymentProvider
from shared.clients.flutterwave_client import FlutterwaveClient


class PaymentProviderFactory:
    """
    Factory for managing multiple payment service providers.
    Supports provider fallback and priority ordering.
    """

    # Provider priority order (highest priority first)
    DEFAULT_PROVIDER_ORDER = ["flutterwave"]

    @staticmethod
    def create_provider(provider_name: str) -> Optional[PaymentProvider]:
        """
        Create a payment provider instance by name.

        Args:
            provider_name: Name of the provider (e.g., "flutterwave", "paystack")

        Returns:
            Provider instance if available and configured, None otherwise
        """
        if provider_name == "flutterwave":
            try:
                return FlutterwaveClient()
            except ValueError:
                return None

        return None

    @staticmethod
    def get_available_providers(
        priority_order: Optional[List[str]] = None
    ) -> List[PaymentProvider]:
        """
        Get all available and configured providers in priority order.

        Args:
            priority_order: Optional custom priority order. If None, uses DEFAULT_PROVIDER_ORDER.

        Returns:
            List of available provider instances, ordered by priority
        """
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
        """
        Get the primary (highest priority) available provider.

        Returns:
            Primary provider instance if available, None otherwise
        """
        providers = PaymentProviderFactory.get_available_providers()
        return providers[0] if providers else None

    @staticmethod
    def get_provider_by_name(provider_name: str) -> Optional[PaymentProvider]:
        """
        Get a specific provider by name, if available.

        Args:
            provider_name: Name of the provider

        Returns:
            Provider instance if available and configured, None otherwise
        """
        provider = PaymentProviderFactory.create_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_provider_for_service(service: str) -> Optional[PaymentProvider]:
        """
        Get the best available provider for a specific service.

        Args:
            service: Service name (e.g., "resolve_account", "initiate_transfer")

        Returns:
            Best available provider for the service, None if none available
        """
        providers = PaymentProviderFactory.get_available_providers()

        if service == "resolve_account":
            # All providers support account resolution
            return providers[0] if providers else None

        elif service == "initiate_transfer":
            # Find a provider that supports transfers
            for provider in providers:
                if provider.supports_transfers:
                    return provider
            return None

        elif service == "get_transfer_status":
            # Find a provider that supports status checks
            for provider in providers:
                if provider.supports_status_checks:
                    return provider
            return None

        elif service == "purchase_airtime":
            # Find a provider that supports airtime purchases
            for provider in providers:
                if hasattr(provider, "supports_airtime") and provider.supports_airtime:
                    return provider
            return None

        return None
