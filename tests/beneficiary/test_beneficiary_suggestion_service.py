import json
from types import SimpleNamespace
from typing import Any

from banking.beneficiaries.services import suggestion_service as service_module
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[tuple[str, ...]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.values[key] = value

    async def delete(self, *keys: str) -> None:
        self.deleted.append(tuple(keys))
        for key in keys:
            self.values.pop(key, None)


class _FakeUsers:
    async def get_by_phone(self, phone_number: str) -> SimpleNamespace:
        del phone_number
        return SimpleNamespace(id="user-1")


class _FakeTransactions:
    def __init__(self) -> None:
        self.updated: list[Any] = []

    async def get_by_id(self, transaction_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=transaction_id)

    async def update(self, transaction: Any, **kwargs: Any) -> None:
        self.updated.append((transaction, kwargs))


class _FakeBeneficiaries:
    def __init__(self, should_suggest: bool = True) -> None:
        self.should_suggest = should_suggest
        self.should_suggest_calls: list[dict[str, Any]] = []
        self.created: dict[str, Any] | None = None

    async def should_suggest_beneficiary(
        self,
        user_id: str,
        account_number: str,
        bank_code: str | None,
        bank_name: str | None = None,
        beneficiary_type: str = "transfer",
    ) -> bool:
        self.should_suggest_calls.append(
            {
                "user_id": user_id,
                "account_number": account_number,
                "bank_code": bank_code,
                "bank_name": bank_name,
                "beneficiary_type": beneficiary_type,
            }
        )
        return self.should_suggest

    async def should_suggest_mobile_beneficiary(
        self,
        user_id: str,
        phone_number: str,
        network: str,
    ) -> bool:
        self.should_suggest_calls.append(
            {
                "user_id": user_id,
                "phone_number": phone_number,
                "network": network,
                "beneficiary_type": "mobile",
            }
        )
        return self.should_suggest

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created = kwargs
        return SimpleNamespace(**kwargs)


class _FakeUnitOfWork:
    def __init__(self, beneficiaries: _FakeBeneficiaries) -> None:
        self.users = _FakeUsers()
        self.transactions = _FakeTransactions()
        self.beneficiaries = beneficiaries
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


def _service(redis: _FakeRedis) -> BeneficiarySuggestionService:
    service = BeneficiarySuggestionService.__new__(BeneficiarySuggestionService)
    service.queue = None
    service.redis_client = redis
    return service


async def test_mono_transfer_suggestion_carries_provider_metadata_and_saves_bank_code(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries()
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    message = await _service(redis).check_and_suggest_beneficiary(
        phone_number="2348011112222",
        beneficiary_type="transfer",
        recipient_data={
            "account_number": "8162511023",
            "bank_code": "044",
            "bank_name": "Access Bank",
            "recipient_bank_code_provider": "mono",
            "recipient_resolution_provider": "mono",
            "name": "Tolu Adebayo",
        },
        transaction_id="tx-1",
        send_message=False,
    )

    assert message is not None
    stored = json.loads(redis.values["user:2348011112222:beneficiary_suggestion"])
    assert stored["recipient_bank_code_provider"] == "mono"
    assert stored["recipient_resolution_provider"] == "mono"
    assert beneficiaries.should_suggest_calls[-1]["bank_code"] == "044"

    saved = await _service(redis).save_beneficiary("2348011112222", alias="Tolu")

    assert "TOLU" in saved
    assert beneficiaries.created is not None
    assert beneficiaries.created["bank_code"] == "044"
    assert beneficiaries.created["bank_name"] == "Access Bank"


async def test_flutterwave_transfer_suggestion_dedupes_by_bank_name_and_saves_without_bank_code(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries()
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    message = await _service(redis).check_and_suggest_beneficiary(
        phone_number="2348011112223",
        beneficiary_type="transfer",
        recipient_data={
            "account_number": "8162511023",
            "bank_code": "000014",
            "bank_name": "Access Bank",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_provider": "flutterwave",
            "name": "Tolu Adebayo",
        },
        transaction_id="tx-2",
        send_message=False,
    )

    assert message is not None
    assert beneficiaries.should_suggest_calls[-1]["bank_code"] is None
    assert beneficiaries.should_suggest_calls[-1]["bank_name"] == "Access Bank"

    await _service(redis).save_beneficiary("2348011112223")

    assert beneficiaries.created is not None
    assert beneficiaries.created["bank_code"] is None
    assert beneficiaries.created["bank_name"] == "Access Bank"
    assert beneficiaries.created["account_number"] == "8162511023"


async def test_legacy_transfer_suggestion_without_provider_metadata_defaults_to_mono(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries()
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    redis.values["user:2348011112224:beneficiary_suggestion"] = json.dumps(
        {
            "beneficiary_type": "transfer",
            "account_number": "8162511023",
            "bank_code": "044",
            "bank_name": "Access Bank",
            "recipient_name": "Tolu Adebayo",
            "alias_suggested": "Tolu",
        }
    )

    await _service(redis).save_beneficiary("2348011112224")

    assert beneficiaries.created is not None
    assert beneficiaries.created["bank_code"] == "044"


async def test_non_mono_duplicate_by_bank_name_is_not_suggested(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries(should_suggest=False)
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    message = await _service(redis).check_and_suggest_beneficiary(
        phone_number="2348011112225",
        beneficiary_type="transfer",
        recipient_data={
            "account_number": "8162511023",
            "bank_code": "000014",
            "bank_name": "Access Bank",
            "recipient_bank_code_provider": "flutterwave",
            "name": "Tolu Adebayo",
        },
        transaction_id="tx-3",
        send_message=False,
    )

    assert message is None
    assert "user:2348011112225:beneficiary_suggestion" not in redis.values
    assert beneficiaries.should_suggest_calls[-1]["bank_code"] is None
    assert beneficiaries.should_suggest_calls[-1]["bank_name"] == "Access Bank"


async def test_mobile_suggestion_uses_human_network_label_but_stores_canonical_network(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries()
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    message = await _service(redis).check_and_suggest_beneficiary(
        phone_number="2348011112226",
        beneficiary_type="data",
        recipient_data={
            "phone": "08031234567",
            "network": "AIRTEL",
            "name": "Tolu",
        },
        transaction_id="tx-4",
        send_message=False,
    )

    assert message is not None
    assert message
    assert "(e.g." in message
    stored = json.loads(redis.values["user:2348011112226:beneficiary_suggestion"])
    assert stored["network"] == "AIRTEL"
    assert beneficiaries.should_suggest_calls[-1]["network"] == "AIRTEL"


async def test_data_suggestion_save_creates_data_mobile_beneficiary(monkeypatch) -> None:
    redis = _FakeRedis()
    beneficiaries = _FakeBeneficiaries()
    uow = _FakeUnitOfWork(beneficiaries)
    monkeypatch.setattr(service_module, "UnitOfWork", lambda: uow)

    message = await _service(redis).check_and_suggest_beneficiary(
        phone_number="2348011112227",
        beneficiary_type="data",
        recipient_data={
            "phone": "08031234567",
            "network": "MTN",
            "name": "Tolu",
        },
        transaction_id="tx-5",
        send_message=False,
    )

    assert message is not None

    saved = await _service(redis).save_beneficiary("2348011112227", alias="Tolu Data")

    assert "TOLU DATA" in saved
    assert beneficiaries.created is not None
    assert beneficiaries.created["account_number"] == "08031234567"
    assert beneficiaries.created["bank_name"] == "MTN"
    assert beneficiaries.created["account_name"] == "Tolu"
    assert beneficiaries.created["beneficiary_type"] == "data"
    assert ("user:2348011112227:beneficiary_suggestion",) in redis.deleted
