from datetime import datetime
from types import SimpleNamespace
from typing import Any

from banking.beneficiaries import worker as worker_module
from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.beneficiaries.worker import BeneficiaryWorker
from banking.runtime.results import TransactionOutcome
from shared.messaging.body_blocks import render_body_blocks_text
from shared.types.conversation_sets import BulkMutationRequest, EntitySelectionRef


class _FakeBeneficiaryRepo:
    def __init__(self, existing: list[Any] | None = None) -> None:
        self.existing = existing or []
        self.created: dict[str, Any] | None = None
        self.deleted: Any | None = None

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created = kwargs
        return SimpleNamespace(**kwargs)

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[Any]:
        del user_id
        if beneficiary_type is not None:
            return [item for item in self.existing if item.beneficiary_type == beneficiary_type]
        return self.existing

    async def delete(self, instance: Any) -> None:
        self.deleted = instance
        if instance in self.existing:
            self.existing.remove(instance)

    async def get_by_ids_for_update(self, user_id: str, beneficiary_ids: list[str]) -> list[Any]:
        del user_id
        return [item for item in self.existing if str(item.id) in beneficiary_ids]


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


async def test_rename_beneficiary_is_id_backed_reviewed_and_alias_only(monkeypatch) -> None:
    updated_at = datetime(2026, 7, 17, 8, 0, 0)
    existing = SimpleNamespace(
        id="bene-1",
        alias="Tolu",
        account_name="Tolu Adebayo",
        bank_name="Access Bank",
        account_number="2010000001",
        beneficiary_type="transfer",
        updated_at=updated_at,
    )
    repo = _FakeBeneficiaryRepo(existing=[existing])
    uow = _FakeUnitOfWork(repo)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: uow)
    ref = EntitySelectionRef(
        entity_type="beneficiary",
        entity_id="bene-1",
        frame_id="beneficiary-list-1",
        display_label="Tolu · Access Bank · ···0001",
        version_token=updated_at.isoformat(),
    )

    review = await BeneficiaryWorker()._rename_beneficiary(
        "user-1",
        {"beneficiary_selection_ref": ref.model_dump(mode="json"), "new_alias": "School Fees"},
        {"language": "en"},
    )
    assert review.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert existing.alias == "Tolu"

    result = await BeneficiaryWorker()._rename_beneficiary(
        "user-1",
        {
            "beneficiary_selection_ref": ref.model_dump(mode="json"),
            "new_alias": "School Fees",
            "confirmation": {"confirmed": True},
        },
        {"language": "en"},
    )
    assert result.outcome == TransactionOutcome.OK
    assert existing.alias == "School Fees"
    assert existing.account_name == "Tolu Adebayo"
    assert existing.account_number == "2010000001"


async def test_delete_beneficiary_is_id_backed_and_reviewed_before_atomic_delete(monkeypatch) -> None:
    updated_at = datetime(2026, 7, 17, 8, 0, 0)
    existing = SimpleNamespace(
        id="bene-1",
        alias="Tolu",
        account_name="Tolu Adebayo",
        bank_name="Access Bank",
        account_number="2010000001",
        updated_at=updated_at,
    )
    repo = _FakeBeneficiaryRepo(existing=[existing])
    uow = _FakeUnitOfWork(repo)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: uow)

    request = BulkMutationRequest(
        domain="beneficiary",
        action="delete",
        targets=[
            EntitySelectionRef(
                entity_type="beneficiary",
                entity_id="bene-1",
                frame_id="beneficiary-list-1",
                display_label="Tolu · Access Bank · ···0001",
                version_token=updated_at.isoformat(),
            )
        ],
        idempotency_key="delete-bene-1",
    )
    review = await BeneficiaryWorker()._delete_beneficiary(
        "user-1",
        {"bulk_mutation": request.model_dump(mode="json")},
        {"language": "en"},
    )

    assert review.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert repo.deleted is None
    assert uow.commit_calls == 0

    result = await BeneficiaryWorker()._delete_beneficiary(
        "user-1",
        {
            "bulk_mutation": request.model_dump(mode="json"),
            "confirmation": {"confirmed": True},
        },
        {"language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert repo.deleted is existing
    assert uow.commit_calls == 1


async def test_list_beneficiaries_count_shape_returns_strict_fact(monkeypatch) -> None:
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
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {"subject": "beneficiary", "response_shape": "fact_count"},
            "beneficiary_contract": {
                "operation": "count",
                "response_shape": "fact_count",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You have 4 saved beneficiaries."
    assert result.read_result is not None
    assert result.read_result.returned_count == 0


async def test_list_beneficiaries_zero_count_uses_natural_copy(monkeypatch) -> None:
    repo = _FakeBeneficiaryRepo(existing=[])
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await BeneficiaryWorker().run(
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {"subject": "beneficiary", "response_shape": "fact_count"},
            "beneficiary_contract": {
                "operation": "count",
                "response_shape": "fact_count",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        result.response
        == "You haven't saved any beneficiaries yet. They will automatically appear here when you choose to save a contact after a successful transfer."
    )
    assert " 0 " not in f" {result.response} "


async def test_filtered_beneficiary_count_excludes_unrelated_saved_people(monkeypatch) -> None:
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
                alias="Landlord",
                account_name="Chidi Okafor",
                bank_name="First Bank",
                account_number="2010000004",
            ),
        ]
    )
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await BeneficiaryWorker().run(
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {
                "subject": "beneficiary",
                "response_shape": "fact_count",
                "entity_name": "Tolu",
            },
            "beneficiary_contract": {
                "operation": "count",
                "response_shape": "fact_count",
                "entity_name": "Tolu",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert result.response == "You have 2 saved beneficiaries matching ‘Tolu’."
    assert result.read_result is not None
    assert result.read_result.total_count == 2
    assert result.details == {}


async def test_named_beneficiary_existence_filters_repository_results(monkeypatch) -> None:
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
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {
                "subject": "beneficiary",
                "response_shape": "fact_bool",
                "entity_name": "Mum",
            },
            "beneficiary_contract": {
                "operation": "existence",
                "response_shape": "fact_bool",
                "entity_name": "Mum",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "Yes, you have a saved beneficiary matching ‘Mum’."
    assert result.read_result is not None
    assert result.read_result.total_count == 1
    assert result.read_result.returned_count == 0


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
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {"subject": "beneficiary", "response_shape": "surface_list"},
            "beneficiary_contract": {
                "operation": "list",
                "response_shape": "surface_list",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response is not None
    assert result.response.startswith("Saved Beneficiaries")
    assert "1. Mum — Mama Nkechi\nOpay • ···1023" in result.response
    assert "2. Tolu Access — Tolu Adebayo\nAccess Bank • ···0001" in result.response
    assert "You have 2 saved beneficiaries." not in result.response


async def test_beneficiary_lists_use_five_item_pages_with_truthful_boundaries(monkeypatch) -> None:
    beneficiaries = [
        SimpleNamespace(
            id=f"bene-{index}",
            alias=f"Person {index}",
            account_name=f"Saved Person {index}",
            bank_name="Access Bank",
            account_number=f"201000000{index}",
        )
        for index in range(1, 8)
    ]
    repo = _FakeBeneficiaryRepo(existing=beneficiaries)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _FakeUnitOfWork(repo))

    first = await BeneficiaryWorker().run(
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {
                "subject": "beneficiary",
                "response_shape": "surface_list",
            },
            "beneficiary_contract": {
                "operation": "list",
                "response_shape": "surface_list",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )
    second = await BeneficiaryWorker().run(
        {
            "action": "list_beneficiaries",
            "intent": "list_beneficiaries",
            "read_request": {
                "subject": "beneficiary",
                "response_shape": "surface_list",
                "offset": 5,
            },
            "beneficiary_contract": {
                "operation": "list",
                "response_shape": "surface_list",
            },
        },
        {"user_id": "user-1", "language": "en"},
    )

    assert first.read_result is not None
    assert first.read_result.returned_count == 5
    assert first.read_result.has_next is True
    assert first.read_result.has_previous is False
    assert first.response is not None and "Person 6" not in first.response
    assert "More for the next page" in first.response

    assert second.read_result is not None
    assert second.read_result.returned_count == 2
    assert second.read_result.has_next is False
    assert second.read_result.has_previous is True
    assert second.response is not None and "Person 6" in second.response and "Person 1" not in second.response


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
        "Saved Beneficiaries\n\n"
        "1. Mum — Mama Nkechi\n"
        "Opay • ···1023\n\n"
        "2. Tolu Access — Tolu Adebayo\n"
        "Access Bank • ···0001"
    )
