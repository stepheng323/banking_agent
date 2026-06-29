from types import SimpleNamespace
from typing import Any

from banking.beneficiaries import worker as worker_module
from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.beneficiaries.worker import BeneficiaryWorker
from banking.runtime.results import TransactionOutcome
from shared.messaging.body_blocks import render_body_blocks_text


class _FakeBeneficiaryRepo:
    def __init__(self, existing: list[Any] | None = None) -> None:
        self.existing = existing or []
        self.created: dict[str, Any] | None = None
        self.deleted: Any | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created = kwargs
        return SimpleNamespace(**kwargs)

    async def get_by_user(self, user_id: str) -> list[Any]:
        del user_id
        return self.existing

    async def delete(self, instance: Any) -> None:
        self.deleted = instance


class _FakeUnitOfWork:
    def __init__(self, repo: _FakeBeneficiaryRepo) -> None:
        self.beneficiaries = repo
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


async def test_add_beneficiary_is_disabled_and_does_not_resolve_or_create(monkeypatch) -> None:
    repo = _FakeBeneficiaryRepo()
    uow = _FakeUnitOfWork(repo)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: uow)

    class _FailingProvider:
        async def get_banks(self) -> None:
            raise AssertionError("manual add should not fetch bank lists")

        async def resolve_account(self, account_number: str, bank_code: str) -> None:
            del account_number, bank_code
            raise AssertionError("manual add should not resolve accounts")

    result = await BeneficiaryWorker()._add_beneficiary(
        "user-1",
        {
            "account_number": "8162511023",
            "bank_name": "Access Bank",
            "account_name": "Tolu Adebayo",
        },
        {"language": "en", "resolver_provider": _FailingProvider()},
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert result.error is not None
    assert "successful transfer" in result.error
    assert repo.created is None
    assert uow.commit_calls == 0


async def test_delete_beneficiary_passes_instance_to_repository_delete(monkeypatch) -> None:
    existing = SimpleNamespace(id="bene-1", alias="Tolu", account_name="Tolu Adebayo")
    repo = _FakeBeneficiaryRepo(existing=[existing])
    uow = _FakeUnitOfWork(repo)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: uow)

    result = await BeneficiaryWorker()._delete_beneficiary(
        "user-1",
        {"alias": "Tolu"},
        {"language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert repo.deleted is existing
    assert uow.commit_calls == 1


async def test_list_beneficiaries_count_shape_returns_count_first_preview(monkeypatch) -> None:
    repo = _FakeBeneficiaryRepo(
        existing=[
            SimpleNamespace(
                id="bene-1",
                alias="Mum",
                account_name="Mama Nkechi",
                bank_name="Opay",
                account_number="8162511023",
            ),
            SimpleNamespace(
                id="bene-2",
                alias="Tolu Access",
                account_name="Tolu Adebayo",
                bank_name="Access Bank",
                account_number="2010000001",
            ),
            SimpleNamespace(
                id="bene-3",
                alias="Tolu GTB",
                account_name="Tolu Adeyemi",
                bank_name="GTBank",
                account_number="2010000002",
            ),
            SimpleNamespace(
                id="bene-4",
                alias="Tolu First",
                account_name="Tolulope Johnson",
                bank_name="First Bank",
                account_number="2010000003",
            ),
        ]
    )
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await BeneficiaryWorker().run(
        {"action": "list_beneficiaries", "intent": "list_beneficiaries", "response_shape": "fact_count"},
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == (
        "You have 4 saved beneficiaries.\n\n"
        "Examples\n\n"
        "1. Mum — Mama Nkechi\n"
        "Opay • ···1023\n\n"
        "2. Tolu Access — Tolu Adebayo\n"
        "Access Bank • ···0001\n\n"
        "3. Tolu GTB — Tolu Adeyemi\n"
        "GTBank • ···0002"
    )
    assert "Tolu First" not in result.response


async def test_list_beneficiaries_zero_count_uses_natural_copy(monkeypatch) -> None:
    repo = _FakeBeneficiaryRepo(existing=[])
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await BeneficiaryWorker().run(
        {"action": "list_beneficiaries", "intent": "list_beneficiaries", "response_shape": "fact_count"},
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You haven't saved any beneficiaries yet. They will automatically appear here when you choose to save a contact after a successful transfer."
    assert " 0 " not in f" {result.response} "


async def test_list_beneficiaries_surface_list_keeps_full_list(monkeypatch) -> None:
    repo = _FakeBeneficiaryRepo(
        existing=[
            SimpleNamespace(
                id="bene-1",
                alias="Mum",
                account_name="Mama Nkechi",
                bank_name="Opay",
                account_number="8162511023",
            ),
            SimpleNamespace(
                id="bene-2",
                alias="Tolu Access",
                account_name="Tolu Adebayo",
                bank_name="Access Bank",
                account_number="2010000001",
            ),
        ]
    )
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await BeneficiaryWorker().run(
        {"action": "list_beneficiaries", "intent": "list_beneficiaries", "response_shape": "surface_list"},
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response is not None
    assert result.response.startswith("Saved beneficiaries")
    assert "1. Mum — Mama Nkechi\nOpay • ···1023" in result.response
    assert "2. Tolu Access — Tolu Adebayo\nAccess Bank • ···0001" in result.response
    assert "You have 2 saved beneficiaries." not in result.response


def test_beneficiary_list_blocks_render_mobile_spacing() -> None:
    blocks = BeneficiaryFormatter.format_beneficiary_list_blocks(
        [
            {
                "alias": "Mum",
                "name": "Mama Nkechi",
                "bank": "Opay",
                "account": "8162511023",
            },
            {
                "alias": "Tolu Access",
                "name": "Tolu Adebayo",
                "bank": "Access Bank",
                "account": "2010000001",
            },
        ],
        locale="en",
    )

    assert blocks is not None
    assert render_body_blocks_text(blocks) == (
        "Saved beneficiaries\n\n"
        "1. Mum — Mama Nkechi\n"
        "Opay • ···1023\n\n"
        "2. Tolu Access — Tolu Adebayo\n"
        "Access Bank • ···0001"
    )
