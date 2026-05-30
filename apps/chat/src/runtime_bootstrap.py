"""Shared runtime startup bootstrap utilities for core processes."""

from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.abstractions.resolution import AccountResolverProvider
from shared.clients.factories.providers import ProviderFactory
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def warm_runtime() -> None:
    """Warm key runtime dependencies for worker and API entrypoints."""
    resolver_providers: list[AccountResolverProvider] = []
    try:
        logger.info("Initializing resolver provider...")
        for flow in ("bootstrap", "payout"):
            resolver_provider = ProviderFactory.get_resolver_for_flow(flow)
            if resolver_provider:
                provider_name = resolver_provider.provider_name
                if not any(existing.provider_name == provider_name for existing in resolver_providers):
                    resolver_providers.append(resolver_provider)
                logger.info(
                    "resolver_provider_ready",
                    flow=flow,
                    provider=provider_name,
                    type=str(type(resolver_provider)),
                )
            else:
                logger.warning("resolver_provider_unavailable", flow=flow)
    except Exception as e:
        logger.warning("Resolver provider initialization warning", error=str(e))

    redis_client = None
    try:
        redis_client = RedisClient.get_client(settings.redis_url)
        logger.info("Redis client initialized")
    except Exception as e:
        logger.warning("Redis client initialization warning", error=str(e))

    if not redis_client:
        return

    try:
        logger.info("Warming up bank cache...")
        if resolver_providers:
            for resolver_provider in resolver_providers:
                bank_cache = BankCacheService(redis_client=redis_client, provider_name=resolver_provider.provider_name)
                banks = await bank_cache.ensure_banks_cached(resolver_provider.get_banks)
                if banks:
                    logger.info("Bank cache ready", provider=resolver_provider.provider_name, count=len(banks))
                else:
                    logger.warning("Bank cache warmup failed", provider=resolver_provider.provider_name)
        else:
            logger.warning("Resolver provider not available for bank list warmup")
    except Exception as e:
        logger.warning("Bank cache warmup warning", error=str(e))
