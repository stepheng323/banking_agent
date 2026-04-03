import asyncio
import time
from types import SimpleNamespace

from shared.cache.user_data import UserDataCache
from shared.services.context_manager import ContextManager


class _FakeUserDataCache:
    def __init__(self, cached_data: dict) -> None:
        self.cached_data = cached_data
        self.set_profile_calls: list[dict] = []
        self.set_accounts_calls: list[list[dict]] = []
        self.set_beneficiaries_calls: list[list[dict]] = []
        self.batch_calls: list[dict] = []

    async def get_all_user_data(self, phone_number: str) -> dict:
        del phone_number
        return self.cached_data

    @staticmethod
    def decode_user_data_fields(**kwargs):
        return UserDataCache.decode_user_data_fields(**kwargs)

    async def set_user_profile(self, phone_number: str, profile: dict) -> None:
        del phone_number
        self.set_profile_calls.append(profile)

    async def set_accounts(self, phone_number: str, accounts: list[dict]) -> None:
        del phone_number
        self.set_accounts_calls.append(accounts)

    async def set_beneficiaries(self, phone_number: str, beneficiaries: list[dict]) -> None:
        del phone_number
        self.set_beneficiaries_calls.append(beneficiaries)

    async def set_user_data_snapshot(
        self,
        phone_number: str,
        *,
        profile: dict | None = None,
        cache_profile: bool = False,
        accounts: list[dict] | None = None,
        cache_accounts: bool = False,
        beneficiaries: list[dict] | None = None,
        cache_beneficiaries: bool = False,
        refresh_snapshot: bool = False,
    ) -> None:
        del phone_number, refresh_snapshot
        payload = {
            "profile": profile,
            "cache_profile": cache_profile,
            "accounts": accounts,
            "cache_accounts": cache_accounts,
            "beneficiaries": beneficiaries,
            "cache_beneficiaries": cache_beneficiaries,
        }
        self.batch_calls.append(payload)
        if cache_profile and profile is not None:
            self.set_profile_calls.append(profile)
        if cache_accounts and accounts is not None:
            self.set_accounts_calls.append(accounts)
        if cache_beneficiaries and beneficiaries is not None:
            self.set_beneficiaries_calls.append(beneficiaries)


class _FakeAccountRepo:
    async def get_by_user(self, user_id: str) -> list[SimpleNamespace]:
        del user_id
        return [
            SimpleNamespace(
                id="acct-1",
                account_number="0000000001",
                bank_name="Test Bank",
                mandate_status="ready",
            )
        ]


class _FakeBeneficiaryRepo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.calls: list[str] = []
        self.search_calls: list[tuple[str, str, str | None]] = []

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[SimpleNamespace]:
        del beneficiary_type
        self.calls.append(user_id)
        return self.rows

    async def search_by_name(
        self,
        user_id: str,
        search_term: str,
        beneficiary_type: str | None = None,
    ) -> list[SimpleNamespace]:
        self.search_calls.append((user_id, search_term, beneficiary_type))
        return self.rows


class _DelayedAccountRepo:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def get_by_user(self, user_id: str) -> list[SimpleNamespace]:
        del user_id
        await asyncio.sleep(self.delay)
        return [
            SimpleNamespace(
                id="acct-1",
                account_number="0000000001",
                bank_name="Test Bank",
                mandate_status="ready",
            )
        ]


class _DelayedBeneficiaryRepo:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def get_by_user(self, user_id: str) -> list[SimpleNamespace]:
        del user_id
        await asyncio.sleep(self.delay)
        return [
            SimpleNamespace(
                id="bene-1",
                beneficiary_type="transfer",
                account_name="Mama Nkechi",
                alias="Mum",
                account_number="2010000002",
                bank_name="GTBank",
                bank_code="058",
            )
        ]


class _FailingRepo:
    async def get_by_phone(self, phone_number: str) -> None:
        raise AssertionError(f"profile lookup should not happen for {phone_number}")


class _PipelineStub:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[tuple[str, tuple]] = []

    def get(self, key: str) -> "_PipelineStub":
        self.calls.append(("get", (key,)))
        return self

    def lrange(self, key: str, start: int, stop: int) -> "_PipelineStub":
        self.calls.append(("lrange", (key, start, stop)))
        return self

    async def execute(self) -> list[object]:
        return self.results


class _RedisStub:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.pipeline_stub = _PipelineStub(results)

    def pipeline(self) -> _PipelineStub:
        return self.pipeline_stub


async def test_load_user_context_full_cache_hit_skips_repos() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": {"id": "user-1"},
            "accounts": [{"id": "acct-cached"}],
            "beneficiaries": [{"id": "bene-cached"}],
        }
    )
    manager = ContextManager(
        user_repo=_FailingRepo(),  # type: ignore[arg-type]
        beneficiary_repo=_FakeBeneficiaryRepo([]),  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context("2348000000100")

    assert ctx == {
        "profile": {"id": "user-1"},
        "accounts": [{"id": "acct-cached"}],
        "beneficiaries": [{"id": "bene-cached"}],
    }
    assert cache.batch_calls == []


async def test_load_user_context_fetches_beneficiaries_when_cache_is_partial() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": {"id": "user-1"},
            "accounts": [{"id": "acct-cached"}],
            "beneficiaries": None,
        }
    )
    beneficiary_repo = _FakeBeneficiaryRepo(
        [
            SimpleNamespace(
                id="bene-1",
                beneficiary_type="transfer",
                account_name="Mama Nkechi",
                alias="Mum",
                account_number="2010000002",
                bank_name="GTBank",
                bank_code="058",
            )
        ]
    )
    manager = ContextManager(
        user_repo=None,
        beneficiary_repo=beneficiary_repo,  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context("2348000000100", user=SimpleNamespace(id="user-1"))

    assert beneficiary_repo.calls == ["user-1"]
    assert ctx["beneficiaries"][0]["alias"] == "Mum"
    assert cache.set_beneficiaries_calls
    assert cache.set_beneficiaries_calls[-1][0]["alias"] == "Mum"


async def test_load_user_context_caches_empty_beneficiaries_list() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": {"id": "user-1"},
            "accounts": [{"id": "acct-cached"}],
            "beneficiaries": None,
        }
    )
    beneficiary_repo = _FakeBeneficiaryRepo([])
    manager = ContextManager(
        user_repo=None,
        beneficiary_repo=beneficiary_repo,  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context("2348000000100", user=SimpleNamespace(id="user-1"))

    assert beneficiary_repo.calls == ["user-1"]
    assert ctx["beneficiaries"] == []
    assert cache.set_beneficiaries_calls
    assert cache.set_beneficiaries_calls[-1] == []


async def test_load_user_context_fetches_accounts_and_beneficiaries_concurrently() -> None:
    cache = _FakeUserDataCache({"profile": {"id": "user-1"}, "accounts": None, "beneficiaries": None})
    manager = ContextManager(
        user_repo=None,
        beneficiary_repo=_DelayedBeneficiaryRepo(0.05),  # type: ignore[arg-type]
        account_repo=_DelayedAccountRepo(0.05),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    started = time.perf_counter()
    ctx = await manager.load_user_context("2348000000100", user=SimpleNamespace(id="user-1"))
    duration = time.perf_counter() - started

    assert duration < 0.09
    assert ctx["accounts"][0]["bank_name"] == "Test Bank"
    assert ctx["beneficiaries"][0]["alias"] == "Mum"
    assert cache.batch_calls


async def test_load_context_parallel_reuses_prefetched_cache_snapshot() -> None:
    redis_stub = _RedisStub(
        [
            None,
            None,
            None,
            "en",
            ['{"role":"assistant","content":"Hi"}'],
            '{"id":"user-1"}',
            '[{"id":"acct-cached"}]',
            '[{"id":"bene-cached"}]',
            None,
        ]
    )
    manager = ContextManager(
        user_repo=_FailingRepo(),  # type: ignore[arg-type]
        beneficiary_repo=_FakeBeneficiaryRepo([]),  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = _FakeUserDataCache({})  # type: ignore[assignment]

    from shared.services import context_manager as context_manager_module

    original_get_client = context_manager_module.RedisClient.get_client
    context_manager_module.RedisClient.get_client = staticmethod(lambda: redis_stub)  # type: ignore[method-assign]
    try:
        user_ctx, conversation_state, last_response, suggestion_data = await manager.load_context_parallel(
            "2348000000100"
        )
    finally:
        context_manager_module.RedisClient.get_client = original_get_client  # type: ignore[method-assign]

    assert conversation_state is None
    assert last_response is None
    assert suggestion_data is None
    assert user_ctx["profile"]["id"] == "user-1"
    assert user_ctx["accounts"] == [{"id": "acct-cached"}]
    assert user_ctx["beneficiaries"] == [{"id": "bene-cached"}]
    assert user_ctx["history"] == [{"role": "assistant", "content": "Hi"}]
    assert user_ctx["language"] == "en"


async def test_load_context_parallel_backfills_from_session_snapshot() -> None:
    redis_stub = _RedisStub(
        [
            None,
            None,
            None,
            "en",
            [],
            None,
            None,
            None,
            '{"profile":{"id":"user-1"},"accounts":[{"id":"acct-snap"}],"beneficiaries":[{"id":"bene-snap"}]}',
        ]
    )
    cache = _FakeUserDataCache({})
    manager = ContextManager(
        user_repo=_FailingRepo(),  # type: ignore[arg-type]
        beneficiary_repo=_FakeBeneficiaryRepo([]),  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    from shared.services import context_manager as context_manager_module

    original_get_client = context_manager_module.RedisClient.get_client
    context_manager_module.RedisClient.get_client = staticmethod(lambda: redis_stub)  # type: ignore[method-assign]
    try:
        user_ctx, _, _, _ = await manager.load_context_parallel("2348000000100")
    finally:
        context_manager_module.RedisClient.get_client = original_get_client  # type: ignore[method-assign]

    assert user_ctx["profile"] == {"id": "user-1"}
    assert user_ctx["accounts"] == [{"id": "acct-snap"}]
    assert user_ctx["beneficiaries"] == [{"id": "bene-snap"}]
    assert cache.batch_calls
    assert cache.batch_calls[-1]["cache_profile"] is True
    assert cache.batch_calls[-1]["cache_accounts"] is True
    assert cache.batch_calls[-1]["cache_beneficiaries"] is True


async def test_load_user_context_minimal_profile_mode_skips_profile_fetch_when_transfer_data_cached() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": None,
            "accounts": [{"id": "acct-cached"}],
            "beneficiaries": [{"id": "bene-cached"}],
        }
    )
    manager = ContextManager(
        user_repo=_FailingRepo(),  # type: ignore[arg-type]
        beneficiary_repo=_FakeBeneficiaryRepo([]),  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context("2348000000100", profile_mode="minimal")

    assert ctx["profile"] is None
    assert ctx["accounts"] == [{"id": "acct-cached"}]
    assert ctx["beneficiaries"] == [{"id": "bene-cached"}]


async def test_load_user_context_cache_only_account_and_beneficiary_modes_skip_fetches() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": None,
            "accounts": None,
            "beneficiaries": None,
        }
    )
    manager = ContextManager(
        user_repo=_FailingRepo(),  # type: ignore[arg-type]
        beneficiary_repo=_FakeBeneficiaryRepo([]),  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context(
        "2348000000100",
        profile_mode="minimal",
        account_mode="cache_only",
        beneficiary_mode="cache_only",
    )

    assert ctx["profile"] is None
    assert ctx["accounts"] == []
    assert ctx["beneficiaries"] == []
    assert cache.batch_calls == []


async def test_load_user_context_cache_only_beneficiary_mode_skips_beneficiary_fetch() -> None:
    cache = _FakeUserDataCache(
        {
            "profile": {"id": "user-1"},
            "accounts": [{"id": "acct-cached"}],
            "beneficiaries": None,
        }
    )
    beneficiary_repo = _FakeBeneficiaryRepo(
        [
            SimpleNamespace(
                id="bene-1",
                beneficiary_type="transfer",
                account_name="Mama Nkechi",
                alias="Mum",
                account_number="2010000002",
                bank_name="GTBank",
                bank_code="058",
            )
        ]
    )
    manager = ContextManager(
        user_repo=None,
        beneficiary_repo=beneficiary_repo,  # type: ignore[arg-type]
        account_repo=_FakeAccountRepo(),  # type: ignore[arg-type]
    )
    manager.data_cache = cache  # type: ignore[assignment]

    ctx = await manager.load_user_context(
        "2348000000100",
        user=SimpleNamespace(id="user-1"),
        beneficiary_mode="cache_only",
    )

    assert beneficiary_repo.calls == []
    assert ctx["beneficiaries"] == []
    assert cache.set_beneficiaries_calls == []
