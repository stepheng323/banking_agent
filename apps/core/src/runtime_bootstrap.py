"""Shared runtime startup bootstrap utilities for core processes."""

from typing import Any

from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.factories.payment import PaymentProviderFactory
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def warm_runtime() -> None:
    """Warm key runtime dependencies for worker and API entrypoints."""
    payment_provider = None
    try:
        logger.info("Initializing payment provider...")
        payment_provider = PaymentProviderFactory.get_provider_for_service("resolve_account")

        if payment_provider:
            logger.info(f"{payment_provider.provider_name.title()} ready", type=str(type(payment_provider)))
        else:
            logger.warning("No payment provider available")
    except Exception as e:
        logger.warning("Payment provider initialization warning", error=str(e))

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

        if payment_provider and hasattr(payment_provider, "get_banks"):

            async def fetch_banks_wrapper() -> list[Any] | None:
                return await payment_provider.get_banks()

            banks = await bank_cache.ensure_banks_cached(fetch_banks_wrapper)
            if banks:
                logger.info("Bank cache ready", count=len(banks))
            else:
                logger.warning("Bank cache warmup failed")
        else:
            logger.warning("Payment provider does not support bank list fetching")
    except Exception as e:
        logger.warning("Bank cache warmup warning", error=str(e))
