from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.transactions.runtime.consumers import direct_transfer_reconciliation_consumer as consumer_module
from banking.transactions.runtime.consumers.direct_transfer_reconciliation_consumer import (
    DirectTransferReconciliationConsumer,
)
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import TransactionStatusEnum


class _Transactions:
    def __init__(self, tx: SimpleNamespace) -> None:
        self.tx = tx
        self.applied: list[dict] = []

    async def get_by_id_for_update(self, transaction_id: str) -> SimpleNamespace | None:
        return self.tx if str(self.tx.id) == transaction_id else None

    async def apply_direct_transfer_result(
        self,
        transaction_id: str,
        *,
        result: DebitResult,
        provider_reference: str,
        commit: bool = True,
    ) -> tuple[SimpleNamespace, str]:
        del commit
        self.applied.append(
            {
                "transaction_id": transaction_id,
                "result": result,
                "provider_reference": provider_reference,
            }
        )
        return self.tx, result.status.value


class _Uow:
    def __init__(self, transactions: _Transactions) -> None:
        self.transactions = transactions
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[SimpleNamespace, str, str | None]] = []

    async def notify(
        self,
        transaction: SimpleNamespace,
        event: str,
        *,
        error_message: str | None = None,
    ) -> None:
        self.calls.append((transaction, event, error_message))


@pytest.mark.asyncio
async def test_direct_transfer_reconciliation_looks_up_mono_by_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = SimpleNamespace(
        id="tx-1",
        status=TransactionStatusEnum.PROCESSING.value,
        idempotency_key="idem-1",
        transaction_id="idem-1",
    )
    transactions = _Transactions(tx)
    provider = SimpleNamespace(
        get_debit_status_by_reference=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="idem-1",
                provider_response={"status": "successful"},
            )
        )
    )
    monkeypatch.setattr(consumer_module, "UnitOfWork", lambda: _Uow(transactions))
    notifier = _Notifier()

    await DirectTransferReconciliationConsumer(provider, notifier=notifier).process_job({"transaction_id": "tx-1"})  # type: ignore[arg-type]

    provider.get_debit_status_by_reference.assert_awaited_once_with("idem-1")
    assert transactions.applied == [
        {
            "transaction_id": "tx-1",
            "result": provider.get_debit_status_by_reference.return_value,
            "provider_reference": "idem-1",
        }
    ]
    assert notifier.calls == [(tx, "successful", None)]


@pytest.mark.asyncio
async def test_direct_transfer_reconciliation_skips_terminal_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = SimpleNamespace(
        id="tx-1",
        status=TransactionStatusEnum.SUCCESSFUL.value,
        idempotency_key="idem-1",
        transaction_id="debit-1",
    )
    transactions = _Transactions(tx)
    provider = SimpleNamespace(get_debit_status_by_reference=AsyncMock())
    monkeypatch.setattr(consumer_module, "UnitOfWork", lambda: _Uow(transactions))

    await DirectTransferReconciliationConsumer(provider).process_job({"transaction_id": "tx-1"})

    provider.get_debit_status_by_reference.assert_not_awaited()
    assert transactions.applied == []
