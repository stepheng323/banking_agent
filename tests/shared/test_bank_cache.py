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
