import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient


async def clear_cache():
    print("Clearing Bank Cache...")
    redis_client = RedisClient.get_client()
    cache = BankCacheService(redis_client)
    res = await cache.clear_cache()
    print(f"Cache cleared: {res}")


if __name__ == "__main__":
    asyncio.run(clear_cache())
