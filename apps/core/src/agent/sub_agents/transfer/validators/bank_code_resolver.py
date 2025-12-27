"""Bank code resolution component."""

from typing import Any

from shared.cache.bank_cache import BankCacheService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BankCodeResolver:
    """Resolves bank names to bank codes using cache."""

    def __init__(self, bank_cache: BankCacheService):
        """
        Initialize resolver.

        Args:
            bank_cache: Bank cache service for code lookups
        """
        self.bank_cache = bank_cache

    async def resolve(
        self,
        bank_name: str,
        fetch_banks_func: Any,
    ) -> str | None:
        """
        Resolve bank name to bank code.

        Args:
            bank_name: Bank name to resolve
            fetch_banks_func: Function to fetch banks if cache is empty

        Returns:
            Bank code if found, None otherwise
        """
        if not bank_name:
            return None

        cache_ready = await self.bank_cache.ensure_banks_cached(fetch_banks_func)
        if not cache_ready:
            logger.warning("bank_cache_not_ready", bank_name=bank_name)
            return None

        bank_code = await self.bank_cache.get_bank_code(bank_name)

        if bank_code:
            logger.debug("bank_code_resolved", bank_name=bank_name, code=bank_code)
        else:
            logger.warning("bank_code_not_found", bank_name=bank_name)

        return bank_code
