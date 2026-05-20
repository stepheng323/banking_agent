from shared.cache.flow_session_manager import FlowSessionManager


class _RedisStub:
    def __init__(self, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail_get = fail_get
        self.fail_set = fail_set

    async def get(self, key: str) -> str | None:
        if self.fail_get:
            raise RuntimeError("redis get failed")
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        if self.fail_set:
            raise RuntimeError("redis set failed")
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


async def test_update_session_strict_verifies_read_back() -> None:
    redis = _RedisStub()
    manager = FlowSessionManager(redis=redis, key_prefix="onboarding")

    ok = await manager.update_session_strict(
        "link-opaque-token",
        {"phone_number": "2348000000000", "step": "method_selection", "is_account_linking": True},
        verify=True,
    )
    read_result = await manager.read_session("link-opaque-token")

    assert ok is True
    assert read_result.found is True
    assert read_result.data == {
        "phone_number": "2348000000000",
        "step": "method_selection",
        "is_account_linking": True,
    }


async def test_read_session_reports_backend_error() -> None:
    manager = FlowSessionManager(redis=_RedisStub(fail_get=True), key_prefix="onboarding")

    result = await manager.read_session("link-opaque-token")

    assert result.backend_error is True
    assert result.error == "redis get failed"


async def test_update_session_strict_returns_false_when_backend_unavailable() -> None:
    manager = FlowSessionManager(redis=_RedisStub(fail_set=True), key_prefix="onboarding")

    ok = await manager.update_session_strict(
        "link-opaque-token",
        {"phone_number": "2348000000000"},
        verify=True,
    )

    assert ok is False
