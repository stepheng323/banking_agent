
import asyncio
import sys
import os
import json
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.core.src.agent.services.user_data_cache import UserDataCache

async def test_caching_layer():
    print("\n--- Testing Redis Caching Layer ---")
    
    # Mock Redis client
    mock_redis = AsyncMock()
    
    # Create cache instance with mock redis
    cache = UserDataCache(redis_client=mock_redis)
    
    phone = "1234567890"
    profile_data = {"id": 1, "name": "Test User", "phone": phone}
    
    # --- Test 1: Cache Miss & Set ---
    print("\n1. Testing Cache Miss & Set...")
    mock_redis.get.return_value = None  # Cache miss
    
    # Mock fetch function
    async def fetch_profile(p):
        print("   Fetching from DB...")
        return profile_data
        
    result = await cache.get_or_set_profile(phone, fetch_profile)
    
    if result == profile_data:
        print("✅ PASS: Correct data returned on cache miss")
    else:
        print("❌ FAIL: Incorrect data returned")
        
    # Verify set was called
    mock_redis.set.assert_called_once()
    args = mock_redis.set.call_args
    if args and args[0][0] == f"cache:user:profile:{phone}" and args[1]['ex'] == 600:
        print("✅ PASS: Data cached with correct key and TTL (600s)")
    else:
        print(f"❌ FAIL: Cache set args incorrect: {args}")
        
    # --- Test 2: Cache Hit ---
    print("\n2. Testing Cache Hit...")
    mock_redis.get.return_value = json.dumps(profile_data)  # Cache hit
    mock_redis.set.reset_mock()
    
    async def fetch_profile_fail(p):
        raise Exception("Should not be called!")
        
    result = await cache.get_or_set_profile(phone, fetch_profile_fail)
    
    if result == profile_data:
        print("✅ PASS: Correct data returned on cache hit")
    else:
        print("❌ FAIL: Incorrect data returned")
        
    mock_redis.set.assert_not_called()
    print("✅ PASS: DB fetch not called on cache hit")
    
    # --- Test 3: Invalidation ---
    print("\n3. Testing Invalidation...")
    await cache.invalidate_user_profile(phone)
    mock_redis.delete.assert_called_with(f"cache:user:profile:{phone}")
    print("✅ PASS: Invalidation called correct delete key")
    
    # --- Test 4: Bulk Load ---
    print("\n4. Testing Bulk Load...")
    mock_redis.mget.return_value = [
        json.dumps(profile_data),
        json.dumps([{"id": 1, "number": "123"}]),  # accounts
        None  # beneficiaries miss
    ]
    
    data = await cache.get_all_user_data(phone)
    
    if data["profile"] == profile_data and len(data["accounts"]) == 1 and data["beneficiaries"] is None:
        print("✅ PASS: Bulk load returned correct mixed results")
    else:
        print(f"❌ FAIL: Bulk load results incorrect: {data}")

if __name__ == "__main__":
    asyncio.run(test_caching_layer())
