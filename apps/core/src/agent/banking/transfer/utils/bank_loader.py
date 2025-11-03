"""Load Nigerian banks from Flutterwave API and manage caching."""

import json
from typing import List, Dict
from pathlib import Path
from shared.clients.payment_provider_factory import PaymentProviderFactory

MINIMAL_FALLBACK_BANKS: List[Dict[str, str]] = [
    {"id": "191", "code": "044", "name": "Access Bank"},
    {"id": "177", "code": "058", "name": "GTBank Plc"},
    {"id": "137", "code": "011", "name": "First Bank of Nigeria"},
    {"id": "141", "code": "057", "name": "Zenith Bank"},
    {"id": "190", "code": "033", "name": "United Bank for Africa"},
]


def get_minimal_fallback() -> List[Dict[str, str]]:
    """Get minimal fallback banks (emergency use only)."""
    return MINIMAL_FALLBACK_BANKS


async def fetch_banks_from_provider(country: str = "NG") -> List[Dict[str, str]]:
    """
    Fetch banks from payment provider API using abstraction layer.

    Uses PaymentProviderFactory to get the appropriate provider,
    making it easy to switch between Flutterwave, Paystack, etc.

    Args:
        country: Country code (e.g., "NG" for Nigeria, "GH" for Ghana)

    Returns:
        List of banks with id, code, and name
    """
    try:

        provider = PaymentProviderFactory.get_provider_for_service(
            service="resolve_account")

        if not provider:
            print("⚠️  No payment provider available")
            return []

        result = await provider.fetch_banks(country=country)

        if result.get("success"):
            banks = result.get("banks", [])
            count = result.get("count", len(banks))
            print(
                f"✅ Fetched {count} banks from {provider.provider_name.upper()} API")
            return banks
        else:
            error = result.get("error", "Unknown error")
            print(f"⚠️  Failed to fetch banks: {error}")
            return []

    except Exception as e:
        print(f"⚠️  Failed to fetch banks from payment provider: {e}")
        return []


async def fetch_and_cache_banks_on_startup(bank_cache) -> List[Dict[str, str]]:
    """
    Fetch banks from payment provider on startup and cache in Redis.

    Strategy (Startup Only):
    1. Always fetch fresh from payment provider API
    2. Cache in Redis with 24h TTL
    3. If provider fails, try existing Redis cache
    4. If both fail, use minimal fallback (5 major banks)

    Args:
        bank_cache: BankCacheService instance

    Returns:
        List of banks with id, code, and name
    """
    print("   🌐 Fetching fresh banks from payment provider API...")
    banks = await fetch_banks_from_provider()

    if banks:
        success = await bank_cache.set_banks(banks, ttl=86400)
        if success:
            print(f"   ✅ Cached {len(banks)} banks in Redis (24h TTL)")
        return banks

    print("   ⚠️  Payment provider fetch failed - checking Redis for existing cache...")
    cached_banks = await bank_cache.get_banks()
    if cached_banks:
        print(f"   📦 Using existing cached banks ({len(cached_banks)} banks)")
        return cached_banks

    print("   ⚠️  Using minimal fallback (5 major banks)")
    fallback_banks = get_minimal_fallback()
    await bank_cache.set_banks(fallback_banks, ttl=3600)

    return fallback_banks


async def get_banks_from_cache(bank_cache) -> List[Dict[str, str]]:
    """
    Get banks from Redis cache (runtime queries).

    During normal operation, all bank queries should come from Redis cache
    which is populated on startup.

    Args:
        bank_cache: BankCacheService instance

    Returns:
        List of banks from cache, or empty list if not available
    """
    cached_banks = await bank_cache.get_banks()
    if cached_banks:
        return cached_banks

    print("⚠️  Bank cache empty - use admin refresh endpoint")
    return []


def save_banks_to_file(banks: List[Dict[str, str]], filepath: str = "banks_cache.json") -> None:
    """Save banks to a local JSON file for caching (deprecated - use Redis)."""
    try:
        path = Path(filepath)
        with path.open("w") as f:
            json.dump(banks, f, indent=2)
        print(f"✅ Saved {len(banks)} banks to {filepath}")
    except Exception as e:
        print(f"⚠️  Failed to save banks: {e}")


def load_banks_from_file(filepath: str = "banks_cache.json") -> List[Dict[str, str]]:
    """Load banks from a local JSON file (deprecated - use Redis)."""
    try:
        path = Path(filepath)
        if path.exists():
            with path.open("r") as f:
                banks = json.load(f)
            print(f"✅ Loaded {len(banks)} banks from {filepath}")
            return banks
    except Exception as e:
        print(f"⚠️  Failed to load banks from file: {e}")

    return get_minimal_fallback()
