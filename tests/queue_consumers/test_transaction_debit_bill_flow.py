from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.transactions.runtime.consumers import bill_fulfillment_consumer as bill_module
from banking.transactions.runtime.consumers import transaction_debit_consumer as debit_module
from banking.transactions.runtime.consumers import transaction_debit_refund_consumer as refund_module
from banking.transactions.runtime.consumers.bill_fulfillment_consumer import BillFulfillmentConsumer
from banking.transactions.runtime.consumers.transaction_debit_consumer import (
    TransactionDebitConsumer,
    TransactionDebitReconciliationConsumer,
)
from banking.transactions.runtime.consumers.transaction_debit_refund_consumer import TransactionDebitRefundConsumer
from banking.transactions.runtime.transaction_debit_helpers import bill_reference_for_transaction
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import TransactionDebitStepStatusEnum, TransactionStatusEnum


class _LedgerAccounts:
    def __init__(self) -> None:
        self.accounts: dict[str, SimpleNamespace] = {}

    async def get_by_code(self, code: str) -> SimpleNamespace | None:
        return self.accounts.get(code)

    async def get_or_create(self, **kwargs) -> SimpleNamespace:
        account = self.accounts.get(kwargs["code"])
        if account:
            return account
        account = SimpleNamespace(id=kwargs["code"], **kwargs)
        self.accounts[kwargs["code"]] = account
        return account


class _LedgerEntries:
    def __init__(self) -> None:
        self.entries: dict[str, SimpleNamespace] = {}

    async def get_by_key(self, entry_key: str) -> SimpleNamespace | None:
        return self.entries.get(entry_key)

    async def create_entry_with_lines(self, **kwargs) -> SimpleNamespace:
        entry = SimpleNamespace(id=kwargs["entry_key"], **kwargs)
        self.entries[kwargs["entry_key"]] = entry
        return entry


class _Transactions:
    def __init__(self, state) -> None:
        self.state = state

    async def get_by_id(self, transaction_id: str) -> SimpleNamespace | None:
        return self.state.tx if transaction_id == str(self.state.tx.id) else None

    async def get_by_id_for_update(self, transaction_id: str) -> SimpleNamespace | None:
        return await self.get_by_id(transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return self.state.tx if idempotency_key == self.state.tx.idempotency_key else None


class _TransactionDebitSteps:
    def __init__(self, state) -> None:
        self.state = state

    async def get_by_transaction_for_update(self, transaction_id: str) -> SimpleNamespace | None:
        return self.state.step if transaction_id == str(self.state.tx.id) else None

    async def get_by_id_for_update(self, step_id: str) -> SimpleNamespace | None:
        return self.state.step if step_id == str(self.state.step.id) else None

    async def get_recoverable_open(self, *, cutoff, limit: int) -> list[SimpleNamespace]:
        del cutoff, limit
        if self.state.step.status in {
            TransactionDebitStepStatusEnum.PENDING.value,
            TransactionDebitStepStatusEnum.PROCESSING.value,
        }:
            return [self.state.step]
        return []

    async def get_confirmed_without_success(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return []

    async def get_or_create_for_transaction(self, **kwargs) -> tuple[SimpleNamespace, bool]:
        del kwargs
        return self.state.step, False

    async def claim_for_debit(self, step_id: str, *, provider_reference: str) -> SimpleNamespace | None:
        step = await self.get_by_id_for_update(step_id)
        if not step or step.status != TransactionDebitStepStatusEnum.PENDING.value:
            return None
        step.status = TransactionDebitStepStatusEnum.PROCESSING.value
        step.provider_reference = provider_reference
        return step

    async def claim_for_refund(self, step_id: str, *, refund_reference: str) -> SimpleNamespace | None:
        step = await self.get_by_id_for_update(step_id)
        if not step or step.status not in {
            TransactionDebitStepStatusEnum.CONFIRMED.value,
            TransactionDebitStepStatusEnum.REFUND_PENDING.value,
        }:
            return None
        step.status = TransactionDebitStepStatusEnum.REFUND_PROCESSING.value
        step.refund_provider_reference = refund_reference
        return step

    async def update_status(self, step_id: str, status: str, **kwargs) -> SimpleNamespace | None:
        step = await self.get_by_id_for_update(step_id)
        if not step:
            return None
        step.status = status
        for key, value in kwargs.items():
            if value is not None:
                setattr(step, key, value)
        return step


class _Accounts:
    def __init__(self, state) -> None:
        self.state = state

    async def get_by_id(self, account_id: str) -> SimpleNamespace | None:
        return self.state.account if account_id == str(self.state.account.id) else None


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def publish(self, *, topic: str, message: dict) -> None:
        self.messages.append((topic, message))


class _State:
    def __init__(self) -> None:
        self.tx = SimpleNamespace(
            id="tx-1",
            user_id="user-1",
            transaction_type="airtime",
            status=TransactionStatusEnum.PROCESSING.value,
            amount=Decimal("2000.00"),
            currency="NGN",
            idempotency_key="idem-1",
            transaction_id=None,
            provider_status=None,
            provider_response=None,
            provider_error_code=None,
            error_message=None,
            completed_at=None,
            target_phone_number="08031234567",
            mobile_network="MTN",
            biller_item_code=None,
            narration="Airtime",
        )
        self.step = SimpleNamespace(
            id="step-1",
            transaction_id="tx-1",
            account_id="acc-1",
            amount=Decimal("2000.00"),
            status=TransactionDebitStepStatusEnum.PENDING.value,
            provider_reference="idem-1-debit",
            provider_debit_id=None,
            refund_provider_id=None,
            refund_provider_reference=None,
            refund_initiated_at=None,
            refund_last_checked_at=None,
            refund_attempt_count=0,
            retry_count=0,
            error_message=None,
            refund_error_message=None,
        )
        self.account = SimpleNamespace(id="acc-1", mandate_id="mandate-1")
        self.ledger_accounts = _LedgerAccounts()
        self.ledger_entries = _LedgerEntries()
        self.publisher = _Publisher()
        self.commit_count = 0


class _Uow:
    def __init__(self, state: _State) -> None:
        self.db = SimpleNamespace(add=lambda obj: None)
        self.transactions = _Transactions(state)
        self.transaction_debit_steps = _TransactionDebitSteps(state)
        self.accounts = _Accounts(state)
        self.ledger_accounts = state.ledger_accounts
        self.ledger_entries = state.ledger_entries
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self._state.commit_count += 1


@pytest.mark.asyncio
async def test_transaction_debit_success_posts_ledger_and_queues_bill(monkeypatch) -> None:
    state = _State()
    monkeypatch.setattr(debit_module, "UnitOfWork", lambda: _Uow(state))
    provider = SimpleNamespace(
        initiate_pooling_debit=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="mono-debit-1",
                reference="idem-1-debit",
                amount=Decimal("2000.00"),
                provider_response={"status": "successful"},
            )
        )
    )

    await TransactionDebitConsumer(provider, state.publisher).process_job({"transaction_id": "tx-1"})

    provider.initiate_pooling_debit.assert_awaited_once()
    assert state.step.status == TransactionDebitStepStatusEnum.CONFIRMED.value
    assert "transaction_debit_step:step-1:mono_debit_confirmed" in state.ledger_entries.entries
    assert state.publisher.messages == [
        (
            "bill.fulfill",
            {"transaction_id": "tx-1", "idempotency_key": "idem-1", "bill_reference": "idem-1-bill"},
        )
    ]


@pytest.mark.asyncio
async def test_transaction_debit_reconciliation_redrives_stale_processing_without_debit_id(monkeypatch) -> None:
    state = _State()
    state.step.status = TransactionDebitStepStatusEnum.PROCESSING.value
    state.step.provider_debit_id = None
    monkeypatch.setattr(debit_module, "UnitOfWork", lambda: _Uow(state))
    provider = SimpleNamespace(
        initiate_pooling_debit=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="mono-debit-1",
                reference="idem-1-debit",
                amount=Decimal("2000.00"),
                provider_response={"status": "successful"},
            )
        ),
        get_debit_status=AsyncMock(),
    )

    await TransactionDebitReconciliationConsumer(provider, state.publisher).process_job({"limit": 10})

    provider.get_debit_status.assert_not_awaited()
    provider.initiate_pooling_debit.assert_awaited_once_with(
        mandate_id="mandate-1",
        amount=Decimal("2000.00"),
        reference="idem-1-debit",
        narration="Airtime",
    )
    assert state.step.status == TransactionDebitStepStatusEnum.CONFIRMED.value
    assert "transaction_debit_step:step-1:mono_debit_confirmed" in state.ledger_entries.entries


@pytest.mark.asyncio
async def test_bill_success_posts_bill_ledger_and_marks_transaction_success(monkeypatch) -> None:
    state = _State()
    state.step.status = TransactionDebitStepStatusEnum.CONFIRMED.value
    monkeypatch.setattr(bill_module, "UnitOfWork", lambda: _Uow(state))
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": True, "reference": "idem-1-bill", "status": "successful"})
    )

    await BillFulfillmentConsumer(provider, state.publisher).process_job({"transaction_id": "tx-1"})

    provider.purchase_airtime.assert_awaited_once_with(
        amount=Decimal("2000.00"),
        recipient_phone="08031234567",
        network="MTN",
        reference=bill_reference_for_transaction(state.tx),
    )
    assert state.tx.status == TransactionStatusEnum.SUCCESSFUL.value
    assert "transaction:tx-1:flutterwave_bill_confirmed" in state.ledger_entries.entries


@pytest.mark.asyncio
async def test_bill_failure_queues_transaction_debit_refund(monkeypatch) -> None:
    state = _State()
    state.step.status = TransactionDebitStepStatusEnum.CONFIRMED.value
    monkeypatch.setattr(bill_module, "UnitOfWork", lambda: _Uow(state))
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "error": "Provider failed", "status": "failed"})
    )

    await BillFulfillmentConsumer(provider, state.publisher).process_job({"transaction_id": "tx-1"})

    assert state.tx.status == TransactionStatusEnum.FAILED.value
    assert state.step.status == TransactionDebitStepStatusEnum.REFUND_PENDING.value
    assert state.publisher.messages[0][0] == "transaction_debit.refund"


@pytest.mark.asyncio
async def test_transaction_debit_refund_success_posts_ledger_and_marks_reversed(monkeypatch) -> None:
    state = _State()
    state.step.status = TransactionDebitStepStatusEnum.REFUND_PENDING.value
    monkeypatch.setattr(refund_module, "UnitOfWork", lambda: _Uow(state))
    provider = SimpleNamespace(
        reverse_debit=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.REVERSED,
                debit_id="refund-1",
                reference="idem-1-debit",
                provider_response={"status": "reversed"},
            )
        )
    )

    await TransactionDebitRefundConsumer(provider).process_job(
        {
            "transaction_debit_step_id": "step-1",
            "transaction_id": "tx-1",
            "original_reference": "idem-1-debit",
        }
    )

    provider.reverse_debit.assert_awaited_once_with("idem-1-debit", reason="Bill payment refund")
    assert state.step.status == TransactionDebitStepStatusEnum.REFUNDED.value
    assert state.tx.status == TransactionStatusEnum.REVERSED.value
    assert "transaction_debit_step:step-1:mono_refund_confirmed" in state.ledger_entries.entries
