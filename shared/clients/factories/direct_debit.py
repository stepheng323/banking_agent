"""Factory for creating DirectDebitProvider instances.

Determines which provider to use based on environment and configuration.
"""

from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DirectDebitProviderFactory:
    """
    Factory for DirectDebitProvider instances.

    Selects the appropriate provider based on:
    - Environment (development uses mock)
    - Configuration (which real provider to use)
    """

    _instance: DirectDebitProvider | None = None

    @classmethod
    def get_provider(cls, provider_name: str | None = None) -> DirectDebitProvider:
        """
        Get the appropriate DirectDebitProvider.

        Args:
            provider_name: Override provider selection ("mono", "mock").
                          If None, uses environment to decide.

        Returns:
            DirectDebitProvider instance
        """
        if cls._instance is not None and provider_name is None:
            return cls._instance

        if provider_name:
            selected = provider_name
        elif settings.app_env == "development":
            selected = "mock"
        else:
            selected = "mono"

        provider = cls._create_provider(selected)

        if provider_name is None:
            cls._instance = provider

        logger.info("direct_debit_provider_created", provider=selected)
        return provider

    @classmethod
    def _create_provider(cls, provider_name: str) -> DirectDebitProvider:
        """Create a provider by name."""
        if provider_name == "mock":
            from shared.clients.providers.mock.direct_debit import MockDirectDebitProvider

            return MockDirectDebitProvider()

        elif provider_name == "mono":
            from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider

            return MonoDirectDebitProvider()

        else:
            raise ValueError(f"Unknown direct debit provider: {provider_name}")

    @classmethod
    def clear_cache(cls) -> None:
        cls._instance = None


def get_direct_debit_provider(provider_name: str | None = None) -> DirectDebitProvider:
    return DirectDebitProviderFactory.get_provider(provider_name)
