"""Factory for creating DirectDebitProvider instances.

Determines which provider to use based on environment and configuration.
"""
from typing import Optional

from shared.config.settings import settings
from shared.clients.direct_debit.base import DirectDebitProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DirectDebitProviderFactory:
    """
    Factory for DirectDebitProvider instances.
    
    Selects the appropriate provider based on:
    - Environment (development uses mock)
    - Configuration (which real provider to use)
    """
    
    # Singleton instance cache
    _instance: Optional[DirectDebitProvider] = None
    
    @classmethod
    def get_provider(cls, provider_name: Optional[str] = None) -> DirectDebitProvider:
        """
        Get the appropriate DirectDebitProvider.
        
        Args:
            provider_name: Override provider selection ("mono", "mock").
                          If None, uses environment to decide.
        
        Returns:
            DirectDebitProvider instance
        """
        # Use cached instance if available and no specific provider requested
        if cls._instance is not None and provider_name is None:
            return cls._instance
        
        # Determine which provider to use
        if provider_name:
            selected = provider_name
        elif settings.app_env == "development":
            selected = "mock"
        else:
            selected = "mono"  # Default production provider
        
        # Create provider
        provider = cls._create_provider(selected)
        
        # Cache if no specific override
        if provider_name is None:
            cls._instance = provider
        
        logger.info("direct_debit_provider_created", provider=selected)
        return provider
    
    @classmethod
    def _create_provider(cls, provider_name: str) -> DirectDebitProvider:
        """Create a provider by name."""
        if provider_name == "mock":
            from shared.clients.direct_debit.mock_provider import MockDirectDebitProvider
            return MockDirectDebitProvider()
        
        elif provider_name == "mono":
            from shared.clients.direct_debit.mono_provider import MonoDirectDebitProvider
            return MonoDirectDebitProvider()
        
        else:
            raise ValueError(f"Unknown direct debit provider: {provider_name}")
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached provider (useful for testing)."""
        cls._instance = None


# Convenience function
def get_direct_debit_provider(provider_name: Optional[str] = None) -> DirectDebitProvider:
    """Get the configured DirectDebitProvider."""
    return DirectDebitProviderFactory.get_provider(provider_name)
