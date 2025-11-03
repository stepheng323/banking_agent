"""Admin endpoints for manual operations."""

from fastapi import APIRouter, BackgroundTasks, HTTPException
import redis.asyncio as redis

from shared.config import settings
from shared.cache import BankCacheService
from apps.core.src.agent.banking.transfer.utils import (
    load_banks_from_flutterwave_response,
    fetch_banks_from_provider
)

router = APIRouter(prefix="/admin", tags=["admin"])

# Global references (will be set during app startup)
_redis_client: redis.Redis = None
_bank_cache: BankCacheService = None


def set_redis_client(client: redis.Redis):
    """Set the global Redis client (called from main.py startup)."""
    global _redis_client, _bank_cache
    _redis_client = client
    if client:
        _bank_cache = BankCacheService(client)


async def _refresh_banks_task():
    """Background task to refresh banks cache."""
    try:
        if not _bank_cache:
            print("⚠️  Bank cache not initialized")
            return

        print("🔄 Admin refresh: Fetching banks from payment provider...")
        banks = await fetch_banks_from_provider()

        if banks:
            # Update Redis cache
            await _bank_cache.set_banks(banks, ttl=86400)

            # Update in-memory cache for normalizer
            load_banks_from_flutterwave_response(banks)

            print(f"✅ Admin refresh: Updated {len(banks)} banks")
        else:
            print("⚠️  Admin refresh: Failed to fetch banks from payment provider")
    except Exception as e:
        print(f"❌ Admin refresh error: {e}")


@router.post("/banks/refresh")
async def refresh_banks_cache(background_tasks: BackgroundTasks):
    """
    Manually refresh banks cache from payment provider API.

    This endpoint triggers a background task to:
    1. Fetch fresh bank data from payment provider
    2. Update Redis cache with 24h TTL
    3. Update in-memory cache for immediate use

    Returns:
        Status message indicating refresh has been initiated
    """
    if not _bank_cache:
        raise HTTPException(
            status_code=503,
            detail="Bank cache service not available"
        )

    background_tasks.add_task(_refresh_banks_task)

    return {
        "status": "initiated",
        "message": "Bank cache refresh initiated in background"
    }


@router.get("/banks/status")
async def get_banks_cache_status():
    """
    Get status of banks cache.

    Returns:
        Cache statistics including last update time and whether cache is available
    """
    if not _bank_cache:
        return {
            "status": "unavailable",
            "message": "Bank cache service not initialized"
        }

    try:
        banks = await _bank_cache.get_banks()
        last_updated = await _bank_cache.get_last_updated()

        return {
            "status": "available",
            "cached": banks is not None,
            "bank_count": len(banks) if banks else 0,
            "last_updated": last_updated,
            "ttl": "24 hours"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@router.delete("/banks/clear")
async def clear_banks_cache():
    """
    Clear banks cache from Redis.

    This will force a fresh fetch from payment provider on next request.

    Returns:
        Status message indicating cache was cleared
    """
    if not _bank_cache:
        raise HTTPException(
            status_code=503,
            detail="Bank cache service not available"
        )

    try:
        success = await _bank_cache.clear_cache()

        if success:
            return {
                "status": "cleared",
                "message": "Bank cache cleared successfully"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to clear cache"
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error clearing cache: {str(e)}"
        )
