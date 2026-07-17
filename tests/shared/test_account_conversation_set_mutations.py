from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from banking.accounts.management import worker as worker_module
from banking.accounts.management.worker import AccountWorker
from banking.runtime.results import AccountOutcome
from shared.types.conversation_sets import BulkMutationRequest, EntitySelectionRef


class _AccountRepo:
    def __init__(self, accounts: list[Any]) -> None:
        self.accounts = accounts
        self.deleted: list[str] = []
        self.default_id: str | None = None

    async def get_by_user(self, user_id: str) -> list[Any]:
        del user_id
        return self.accounts

    async def get_by_user_for_update(self, user_id: str) -> list[Any]:
        del user_id
        return self.accounts

    async def delete_account(
        self,
        account_id: str,
        user_id: str,
        *,
        assign_new_default: bool = True,
    ) -> bool:
        del user_id, assign_new_default
        self.deleted.append(account_id)
        self.accounts = [account for account in self.accounts if account.account_id != account_id]
        return True

    async def set_default_account(self, user_id: str, account_id: str) -> Any:
        del user_id
        self.default_id = account_id
        return next(account for account in self.accounts if account.account_id == account_id)


class _Uow:
    def __init__(self, repo: _AccountRepo) -> None:
        self.accounts = repo
        self.commits = 0

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False

    async def commit(self) -> None:
        self.commits += 1


class _Provider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def cancel_mandate(self, mandate_id: str) -> bool:
        self.calls.append(mandate_id)
        if mandate_id == "mandate-fail":
            raise RuntimeError("provider unavailable")
        return True


def _account(index: int, *, is_default: bool = False, mandate_id: str | None = None) -> Any:
    return SimpleNamespace(
        account_id=f"account-{index}",
        id=f"db-{index}",
        bank_name=f"Bank {index}",
        account_number=f"000000000{index}",
        is_default=is_default,
        mandate_id=mandate_id,
        updated_at=datetime(2026, 7, 17, 8, index, 0),
    )


def _request(accounts: list[Any]) -> BulkMutationRequest:
    return BulkMutationRequest(
        domain="linked_account",
        action="unlink",
        targets=[
            EntitySelectionRef(
                entity_type="linked_account",
                entity_id=account.account_id,
                frame_id="account-frame",
                display_label=f"{account.bank_name} · ···{account.account_number[-4:]}",
                version_token=account.updated_at.isoformat(),
            )
            for account in accounts
        ],
        idempotency_key="unlink-set-1",
    )


def _worker(repo: _AccountRepo, provider: _Provider) -> AccountWorker:
    return AccountWorker(
        account_repo=repo,
        user_repo=SimpleNamespace(),
        llm=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        session_manager=SimpleNamespace(),
        direct_debit_provider=provider,
    )


async def test_account_unlink_reviews_then_reports_provider_partial_failure(monkeypatch) -> None:
    first = _account(1, mandate_id="mandate-ok")
    second = _account(2, mandate_id="mandate-fail")
    remaining = _account(3, is_default=True)
    repo = _AccountRepo([first, second, remaining])
    provider = _Provider()
    worker = _worker(repo, provider)
    request = _request([first, second])
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _Uow(repo))

    review = await worker._unlink_reviewed_accounts(
        user_id="user-1",
        payload={"bulk_mutation": request.model_dump(mode="json")},
        locale="en",
        patch={},
    )
    assert review.outcome == AccountOutcome.NEEDS_CONFIRMATION
    assert provider.calls == []
    assert repo.deleted == []

    result = await worker._unlink_reviewed_accounts(
        user_id="user-1",
        payload={
            "bulk_mutation": request.model_dump(mode="json"),
            "confirmation": {"confirmed": True},
        },
        locale="en",
        patch={},
    )

    assert result.outcome == AccountOutcome.OK
    assert provider.calls == ["mandate-ok", "mandate-fail"]
    assert repo.deleted == ["account-1"]
    assert "Bank 1" in (result.response or "") and "Bank 2" in (result.response or "")


async def test_account_unlink_default_requires_explicit_remaining_default(monkeypatch) -> None:
    selected = _account(1, is_default=True)
    remaining = _account(2)
    repo = _AccountRepo([selected, remaining])
    worker = _worker(repo, _Provider())
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _Uow(repo))

    result = await worker._unlink_reviewed_accounts(
        user_id="user-1",
        payload={"bulk_mutation": _request([selected]).model_dump(mode="json")},
        locale="en",
        patch={},
    )

    assert result.outcome == AccountOutcome.NEEDS_INPUT
    assert result.required_fields == ["identifier"]


async def test_fresh_single_unlink_is_converted_to_versioned_review_request(monkeypatch) -> None:
    selected = _account(1)
    remaining = _account(2, is_default=True)
    repo = _AccountRepo([selected, remaining])
    provider = _Provider()
    worker = _worker(repo, provider)
    monkeypatch.setattr(worker_module, "UnitOfWork", lambda: _Uow(repo))

    request = await worker._build_direct_unlink_request(
        user_id="user-1",
        identifier="Bank 1",
        locale="en",
        patch={},
    )

    assert isinstance(request, BulkMutationRequest)
    assert request.targets[0].entity_id == "account-1"
    assert request.targets[0].version_token == selected.updated_at.isoformat()
    review = await worker._unlink_reviewed_accounts(
        user_id="user-1",
        payload={"bulk_mutation": request.model_dump(mode="json")},
        locale="en",
        patch={},
    )
    assert review.outcome == AccountOutcome.NEEDS_CONFIRMATION
    assert repo.deleted == []
    assert provider.calls == []
