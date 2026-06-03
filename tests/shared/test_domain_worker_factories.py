from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from banking.accounts.management.worker import AccountWorker
from banking.accounts.runtime import build_account_worker
from banking.beneficiaries.runtime import build_beneficiary_worker
from banking.beneficiaries.worker import BeneficiaryWorker
from banking.bills.airtime.runtime import build_airtime_worker
from banking.bills.airtime.worker import AirtimeWorker
from banking.bills.data.runtime import build_data_worker
from banking.bills.data.worker import DataWorker
from banking.faq.runtime import build_faq_worker
from banking.faq.worker import FAQWorker
from banking.runtime.protocols import WorkerProtocol
from banking.support.runtime import build_support_worker
from banking.support.worker import SupportWorker
from banking.transactions.query.runtime import build_query_worker
from banking.transactions.query.session import QuerySessionManager
from banking.transactions.query.worker import QueryWorker
from banking.transfers.runtime import build_transfer_worker
from banking.transfers.worker import TransferWorker


class _LLMStub:
    def with_structured_output(self, schema: object) -> _LLMStub:
        del schema
        return self

    async def ainvoke(self, prompt: object) -> SimpleNamespace:
        del prompt
        return SimpleNamespace(content="{}")


class _RedisStub:
    async def get(self, key: str) -> None:
        del key
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del key, value, ex
        return True

    async def delete(self, key: str) -> int:
        del key
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        del key, ttl
        return True


class _BankingProviderStub:
    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


def test_faq_factory_preserves_worker_protocol(monkeypatch) -> None:
    monkeypatch.setattr("banking.faq.worker.EmbeddingService", lambda: object())
    worker = build_faq_worker(llm=_LLMStub(), get_db=lambda: object())

    assert isinstance(worker, FAQWorker)
    assert worker.__class__.__module__ == "banking.faq.worker"
    assert isinstance(worker, WorkerProtocol)


def test_beneficiary_factory_preserves_worker_protocol() -> None:
    worker = build_beneficiary_worker()

    assert isinstance(worker, BeneficiaryWorker)
    assert worker.__class__.__module__ == "banking.beneficiaries.worker"
    assert isinstance(worker, WorkerProtocol)


def test_account_factory_preserves_worker_protocol() -> None:
    worker = build_account_worker(
        account_repo=object(),  # type: ignore[arg-type]
        user_repo=object(),  # type: ignore[arg-type]
        llm=_LLMStub(),  # type: ignore[arg-type]
        banking_provider=object(),  # type: ignore[arg-type]
        session_manager=object(),  # type: ignore[arg-type]
        direct_debit_provider=object(),  # type: ignore[arg-type]
    )

    assert isinstance(worker, AccountWorker)
    assert worker.__class__.__module__ == "banking.accounts.management.worker"
    assert isinstance(worker, WorkerProtocol)


def test_airtime_factory_preserves_worker_protocol() -> None:
    worker = build_airtime_worker(
        extractor_llm=_LLMStub(),  # type: ignore[arg-type]
        bill_provider=object(),
        transaction_repo=object(),
        publisher=object(),
        redis_client=_RedisStub(),
    )

    assert isinstance(worker, AirtimeWorker)
    assert worker.__class__.__module__ == "banking.bills.airtime.worker"
    assert isinstance(worker, WorkerProtocol)


def test_data_factory_preserves_worker_protocol() -> None:
    worker = build_data_worker(
        extractor_llm=_LLMStub(),  # type: ignore[arg-type]
        bill_provider=object(),
        transaction_repo=object(),
        publisher=object(),
        redis_client=_RedisStub(),
    )

    assert isinstance(worker, DataWorker)
    assert worker.__class__.__module__ == "banking.bills.data.worker"
    assert isinstance(worker, WorkerProtocol)


def test_transfer_factory_preserves_worker_protocol() -> None:
    worker = build_transfer_worker(
        extractor_llm=_LLMStub(),  # type: ignore[arg-type]
        publisher=object(),
        resolver_provider=object(),
        bank_cache=object(),
        transaction_repo=object(),
        dd_provider=object(),
        redis_client=_RedisStub(),
        payout_resolver_provider=object(),
        payout_bank_cache=object(),
    )

    assert isinstance(worker, TransferWorker)
    assert worker.__class__.__module__ == "banking.transfers.worker"
    assert isinstance(worker, WorkerProtocol)


def test_support_factory_preserves_worker_protocol() -> None:
    worker = build_support_worker(
        llm=_LLMStub(),
        transaction_repo=object(),
        actionable_message_repo=object(),
        redis_client=_RedisStub(),
    )

    assert isinstance(worker, SupportWorker)
    assert worker.__class__.__module__ == "banking.support.worker"
    assert isinstance(worker, WorkerProtocol)


def test_query_factory_preserves_worker_protocol() -> None:
    worker = build_query_worker(
        llm=_LLMStub(),
        banking_provider=_BankingProviderStub(),  # type: ignore[arg-type]
        redis_client=_RedisStub(),  # type: ignore[arg-type]
    )

    assert isinstance(worker, QueryWorker)
    assert worker.__class__.__module__ == "banking.transactions.query.worker"
    assert isinstance(worker.session_manager, QuerySessionManager)
    assert isinstance(worker, WorkerProtocol)
