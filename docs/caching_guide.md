# User Data Caching - Usage Guide

## Overview

The `UserDataCache` service provides Redis caching for frequently accessed user data to reduce database queries.

## What Gets Cached

| Data Type     | Cache Key                          | TTL    | When to Invalidate                       |
| ------------- | ---------------------------------- | ------ | ---------------------------------------- |
| User Profile  | `cache:user:profile:{phone}`       | 10 min | Profile updates, settings changes        |
| Accounts      | `cache:user:accounts:{phone}`      | 5 min  | Account linked/unlinked, balance changes |
| Beneficiaries | `cache:user:beneficiaries:{phone}` | 5 min  | Beneficiary added/removed/updated        |

## Usage Examples

### 1. Basic Usage - Cache-Aside Pattern

```python
from apps.core.src.agent.services.user_data_cache import UserDataCache

cache = UserDataCache()

# Try cache first, fetch from DB on miss
async def get_user_accounts(phone_number: str):
    accounts = await cache.get_or_set_accounts(
        phone_number,
        fetch_fn=lambda phone: user_repo.get_accounts_by_phone(phone)
    )
    return accounts
```

### 2. Manual Cache Management

```python
# Get from cache
profile = await cache.get_user_profile(phone_number)

if not profile:
    # Cache miss - fetch from DB
    profile = await user_repo.get_by_phone(phone_number)

    # Save to cache
    if profile:
        await cache.set_user_profile(phone_number, profile)
```

### 3. Cache Invalidation

```python
# When user updates profile
await user_repo.update_profile(phone_number, new_data)
await cache.invalidate_user_profile(phone_number)  # Clear cache

# When account is linked
await account_service.link_account(phone_number, account_data)
await cache.invalidate_accounts(phone_number)  # Clear cache

# Clear all user data
await cache.invalidate_all_user_data(phone_number)
```

### 4. Bulk Loading (Parallel)

```python
# Get all user data in one call
data = await cache.get_all_user_data(phone_number)

profile = data["profile"]
accounts = data["accounts"]
beneficiaries = data["beneficiaries"]

# Each will be None if not cached
if not profile:
    profile = await fetch_profile_from_db(phone_number)
    await cache.set_user_profile(phone_number, profile)
```

## Integration Points

### Where to Add Caching

1. **Context Manager** (`context_manager.py`)

   ```python
   async def load_user_context(self, phone_number: str):
       cache = UserDataCache()

       # Try cache first
       cached_data = await cache.get_all_user_data(phone_number)

       if cached_data["profile"]:
           return cached_data  # Cache hit

       # Cache miss - fetch from DB
       profile = await self.user_repo.get_by_phone(phone_number)
       accounts = await self.user_repo.get_accounts(phone_number)

       # Cache for next time
       if profile:
           await cache.set_user_profile(phone_number, profile)
       if accounts:
           await cache.set_accounts(phone_number, accounts)

       return {"profile": profile, "accounts": accounts}
   ```

2. **Beneficiary Handler** (`beneficiary_handler.py`)

   ```python
   async def suggest_beneficiaries(self, phone_number: str):
       cache = UserDataCache()

       # Use cache-aside pattern
       beneficiaries = await cache.get_or_set_beneficiaries(
           phone_number,
           fetch_fn=self.user_repo.get_beneficiaries
       )

       return self._generate_suggestions(beneficiaries)
   ```

3. **Transfer Service** (`transfer/service.py`)
   ```python
   async def validate_transfer(self, phone_number: str):
       cache = UserDataCache()

       # Get accounts with caching
       accounts = await cache.get_or_set_accounts(
           phone_number,
           fetch_fn=lambda phone: self.user_repo.get_accounts(phone)
       )

       # ...validation logic
   ```

## Performance Impact

### Before Caching

```
Every message:
- DB query for profile: ~200ms
- DB query for accounts: ~300ms
- DB query for beneficiaries: ~200ms
Total: ~700ms per message
```

### After Caching

```
First message: ~700ms (cache miss, fetch from DB)
Subsequent messages: ~20ms (cache hit from Redis)
Improvement: ~680ms (97% faster)
```

## Cache Warming Strategy

### On User Login/Registration

```python
async def on_user_login(phone_number: str):
    cache = UserDataCache()

    # Warm up cache immediately
    profile = await user_repo.get_by_phone(phone_number)
    accounts = await user_repo.get_accounts(phone_number)
    beneficiaries = await user_repo.get_beneficiaries(phone_number)

    # Cache all data
    await cache.set_user_profile(phone_number, profile)
    await cache.set_accounts(phone_number, accounts)
    await cache.set_beneficiaries(phone_number, beneficiaries)
```

## Monitoring

### Check Cache Hit Rates

```python
# Get cache stats for a user
stats = await cache.get_cache_stats(phone_number)
print(f"Profile cached: {stats['profile_cached']}")
print(f"Accounts cached: {stats['accounts_cached']}")
print(f"Beneficiaries cached: {stats['beneficiaries_cached']}")
```

### Add Metrics (Future)

```python
cache_hits = 0
cache_misses = 0

# Track in get_or_set methods
if cached:
    cache_hits += 1
else:
    cache_misses += 1

hit_rate = cache_hits / (cache_hits + cache_misses)
```

## Important Notes

1. **TTL Selection**: Current TTLs are conservative. Adjust based on data change frequency.

2. **Invalidation**: Always invalidate when data changes to prevent stale data.

3. **Serialization**: Use `json.dumps/loads` for dict/list data. For complex objects, use custom serializer.

4. **Memory**: Monitor Redis memory usage. Cached data is small (~1-5KB per user).

5. **Consistency**: Cache provides eventual consistency. Critical reads may still need DB.

## Next Steps

- [ ] Integrate into ContextManager
- [ ] Add to BeneficiaryHandler
- [ ] Monitor cache hit rates
- [ ] Adjust TTLs based on usage patterns
