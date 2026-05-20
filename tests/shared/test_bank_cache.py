from shared.cache.bank_cache import BankCacheService
from shared.clients.abstractions.resolution import BankListResult, BankRecord


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        del ttl
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def test_ensure_banks_cached_accepts_typed_result() -> None:
    cache = BankCacheService(redis_client=_FakeRedis())

    async def _fetch() -> BankListResult:
        return BankListResult(
            success=True,
            provider="mono",
            banks=[
                BankRecord(code="044", name="Access Bank"),
                BankRecord(code="058", name="GTBank"),
            ],
        )

    banks = await cache.ensure_banks_cached(_fetch)

    assert banks == [
        {"code": "044", "name": "Access Bank"},
        {"code": "058", "name": "GTBank"},
    ]
    assert await cache.get_bank_code("access bank") == "044"


async def test_bank_cache_is_scoped_by_provider_and_country() -> None:
    redis = _FakeRedis()
    mono_cache = BankCacheService(redis_client=redis, provider_name="mono", country="NG")
    flutterwave_cache = BankCacheService(redis_client=redis, provider_name="flutterwave", country="NG")

    assert await mono_cache.set_banks([{"code": "044", "name": "Access Bank"}])
    assert await flutterwave_cache.set_banks([{"code": "000014", "name": "Access Bank"}])

    assert "bank_directory:mono:NG" in redis.store
    assert "bank_directory:flutterwave:NG" in redis.store
    assert "nigerian_banks" not in redis.store
    assert await mono_cache.get_bank_code("access bank") == "044"
    assert await flutterwave_cache.get_bank_code("access bank") == "000014"


async def test_bank_cache_miss_fetches_only_for_its_provider() -> None:
    redis = _FakeRedis()
    mono_calls = 0
    flutterwave_calls = 0
    mono_cache = BankCacheService(redis_client=redis, provider_name="mono")
    flutterwave_cache = BankCacheService(redis_client=redis, provider_name="flutterwave")

    async def _fetch_mono() -> BankListResult:
        nonlocal mono_calls
        mono_calls += 1
        return BankListResult(success=True, provider="mono", banks=[BankRecord(code="044", name="Access Bank")])

    async def _fetch_flutterwave() -> BankListResult:
        nonlocal flutterwave_calls
        flutterwave_calls += 1
        return BankListResult(
            success=True,
            provider="flutterwave",
            banks=[BankRecord(code="000014", name="Access Bank")],
        )

    assert await mono_cache.ensure_banks_cached(_fetch_mono) == [{"code": "044", "name": "Access Bank"}]
    assert await flutterwave_cache.ensure_banks_cached(_fetch_flutterwave) == [
        {"code": "000014", "name": "Access Bank"}
    ]

    assert mono_calls == 1
    assert flutterwave_calls == 1
    assert await mono_cache.get_bank_code("Access") == "044"
    assert await flutterwave_cache.get_bank_code("Access") == "000014"
