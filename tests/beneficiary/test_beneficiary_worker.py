from types import SimpleNamespace
from typing import Any

from apps.chat.src.agent.graphs.beneficiary import worker as worker_module
from apps.chat.src.agent.graphs.beneficiary.worker import BeneficiaryWorker
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome


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
