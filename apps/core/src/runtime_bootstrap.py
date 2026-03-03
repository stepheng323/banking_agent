"""Shared runtime startup bootstrap utilities for core processes."""

from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.factories.providers import ProviderFactory
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def warm_runtime() -> None:
    """Warm key runtime dependencies for worker and API entrypoints."""
    resolver_provider = None
    try:
        logger.info("Initializing resolver provider...")
        resolver_provider = ProviderFactory.get_resolver_for_flow("bootstrap")

        if resolver_provider:
            logger.info(f"{resolver_provider.provider_name.title()} ready", type=str(type(resolver_provider)))
        else:
            logger.warning("No resolver provider available")
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
        bank_cache = BankCacheService(redis_client=redis_client)

        if resolver_provider:
            banks = await bank_cache.ensure_banks_cached(resolver_provider.get_banks)
            if banks:
                logger.info("Bank cache ready", count=len(banks))
            else:
                logger.warning("Bank cache warmup failed")
        else:
            logger.warning("Resolver provider not available for bank list warmup")
    except Exception as e:
        logger.warning("Bank cache warmup warning", error=str(e))
