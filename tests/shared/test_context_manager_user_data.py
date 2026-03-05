from types import SimpleNamespace

from shared.services.context_manager import ContextManager


class _FakeUserDataCache:
    def __init__(self, cached_data: dict) -> None:
        self.cached_data = cached_data
        self.set_profile_calls: list[dict] = []
        self.set_accounts_calls: list[list[dict]] = []
        self.set_beneficiaries_calls: list[list[dict]] = []

    async def get_all_user_data(self, phone_number: str) -> dict:
        del phone_number
        return self.cached_data

    async def set_user_profile(self, phone_number: str, profile: dict) -> None:
        del phone_number
        self.set_profile_calls.append(profile)

    async def set_accounts(self, phone_number: str, accounts: list[dict]) -> None:
        del phone_number
        self.set_accounts_calls.append(accounts)

    async def set_beneficiaries(self, phone_number: str, beneficiaries: list[dict]) -> None:
        del phone_number
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

    async def get_by_user(self, user_id: str) -> list[SimpleNamespace]:
        self.calls.append(user_id)
        return self.rows


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
