from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from banking.ledger import reconciliation as reconciliation_module
from banking.ledger.reconciliation import (
    LedgerExposureReconciliationConsumer,
    LedgerPostingReconciliationConsumer,
)
from shared.database.enums import (
    FundedTransferStatusEnum,
    FundingStepStatusEnum,
    TransactionDebitStepStatusEnum,
    TransactionStatusEnum,
)


class _LedgerAccounts:
    def __init__(self, balance: Decimal = Decimal("0.00")) -> None:
        self.accounts: dict[str, SimpleNamespace] = {}
        self.balance = balance

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
    def __init__(self, accounts: _LedgerAccounts) -> None:
        self.accounts = accounts
        self.entries: dict[str, SimpleNamespace] = {}

    async def get_by_key(self, entry_key: str) -> SimpleNamespace | None:
        return self.entries.get(entry_key)

    async def create_entry_with_lines(self, **kwargs) -> SimpleNamespace:
        entry = SimpleNamespace(id=kwargs["entry_key"], **kwargs)
        self.entries[kwargs["entry_key"]] = entry
        return entry

    async def liability_balance_for_transfer(self, *, liability_account_id) -> Decimal:
        del liability_account_id
        return self.accounts.balance

    async def liability_balance_for_transaction(self, *, liability_account_id) -> Decimal:
        del liability_account_id
        return self.accounts.balance


class _ReconciliationRepo:
    def __init__(self, state) -> None:
        self.state = state
        self.findings: dict[str, SimpleNamespace] = {}
        self.resolved: list[str] = []
        self.runs: list[SimpleNamespace] = []

    async def create_run(self, *, run_type: str, details: dict | None = None) -> SimpleNamespace:
        run = SimpleNamespace(run_type=run_type, details=details or {}, status="running")
        self.runs.append(run)
        return run

    async def finish_run(self, run: SimpleNamespace, **kwargs) -> SimpleNamespace:
        for key, value in kwargs.items():
            setattr(run, key, value)
        return run

    async def list_confirmed_funding_steps(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.confirmed_steps

    async def list_refunded_funding_steps(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.refunded_steps

    async def list_completed_funded_transfers(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.completed_transfers

    async def list_successful_non_pooled_transactions(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.successful_transactions

    async def list_confirmed_transaction_debit_steps(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.transaction_debit_steps

    async def list_successful_debit_backed_bill_transactions(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.debit_backed_bill_transactions

    async def list_refunded_transaction_debit_steps(self, *, limit: int) -> list[SimpleNamespace]:
        del limit
        return self.state.refunded_transaction_debit_steps

    async def list_transfers_for_exposure_scan(self, *, cutoff: datetime, limit: int) -> list[SimpleNamespace]:
        del cutoff, limit
        return self.state.exposure_transfers

    async def list_transaction_debits_for_exposure_scan(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[SimpleNamespace]:
        del cutoff, limit
        return self.state.exposure_transaction_debit_steps

    async def list_direct_transfer_transactions_for_exposure_scan(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[SimpleNamespace]:
        del cutoff, limit
        return self.state.exposure_direct_transfer_transactions

    async def open_or_refresh_finding(self, **kwargs) -> SimpleNamespace:
        finding = self.findings.get(kwargs["finding_key"])
        if finding:
            for key, value in kwargs.items():
                setattr(finding, key, value)
            finding.status = "open"
        else:
            finding = SimpleNamespace(id=kwargs["finding_key"], status="open", support_ticket_id=None, **kwargs)
            self.findings[kwargs["finding_key"]] = finding
        return finding

    async def resolve_finding(self, finding_key: str) -> None:
        self.resolved.append(finding_key)
        finding = self.findings.get(finding_key)
        if finding:
            finding.status = "resolved"


class _FundedTransfers:
    def __init__(self, transfers: dict[str, SimpleNamespace]) -> None:
        self.transfers = transfers

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfers.get(transfer_id)


class _FundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        return [step for step in self.steps if step.funded_transfer_id == transfer_id]


class _Transactions:
    def __init__(self, state: "_State") -> None:
        self.state = state

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=f"tx:{idempotency_key}")

    async def get_by_id(self, transaction_id: str) -> SimpleNamespace | None:
        for transaction in [
            *self.state.debit_backed_bill_transactions,
            *self.state.successful_transactions,
            *self.state.exposure_direct_transfer_transactions,
        ]:
            if str(transaction.id) == transaction_id:
                return transaction
        return SimpleNamespace(
            id=transaction_id,
            user_id="user-1",
            transaction_type="airtime",
            amount=Decimal("1500.00"),
            currency="NGN",
            idempotency_key="debit-backed-idem-1",
            transaction_id="bill-ref-1",
            provider_status="successful",
            provider_response={"provider": "flutterwave", "status": "successful"},
        )


class _SupportTickets:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def get_by_transaction_ref(self, transaction_ref: str) -> list[SimpleNamespace]:
        del transaction_ref
        return []

    async def generate_ticket_code(self) -> str:
        return "SUP-20260530-0001"

    async def create(self, **kwargs) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id="ticket-1", **kwargs)


class _State:
    def __init__(self) -> None:
        self.transfer = SimpleNamespace(
            id="funded-1",
            user_id="user-1",
            amount=Decimal("5000.00"),
            idempotency_key="idem-1",
            payout_reference="trf-1",
            status=FundedTransferStatusEnum.COMPLETED.value,
            created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
            updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2),
            completed_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
        )
        self.step = SimpleNamespace(
            id="step-1",
            funded_transfer_id="funded-1",
            amount=Decimal("5000.00"),
            status=FundingStepStatusEnum.REFUNDED.value,
            provider_reference="mono-ref-1",
            provider_debit_id="debit-1",
            refund_provider_reference="refund-ref-1",
            refund_provider_id="refund-1",
        )
        self.confirmed_steps = [self.step]
        self.refunded_steps = [self.step]
        self.completed_transfers = [self.transfer]
        self.successful_transactions = [
            SimpleNamespace(
                id="tx-direct-1",
                user_id="user-1",
                transaction_type="transfer",
                amount=Decimal("1500.00"),
                currency="NGN",
                idempotency_key="direct-idem-1",
                transaction_id="mono-direct-1",
                provider_status="successful",
                provider_response={"provider": "mono"},
            )
        ]
        self.transaction_debit_step = SimpleNamespace(
            id="tx-debit-step-1",
            transaction_id="tx-bill-1",
            account_id="acc-1",
            amount=Decimal("1500.00"),
            provider_reference="mono-debit-ref-1",
            provider_debit_id="mono-debit-1",
            refund_provider_reference="mono-refund-ref-1",
            refund_provider_id="mono-refund-1",
        )
        self.transaction_debit_steps = [self.transaction_debit_step]
        self.debit_backed_bill_transactions = [
            SimpleNamespace(
                id="tx-bill-1",
                user_id="user-1",
                transaction_type="airtime",
                status=TransactionStatusEnum.SUCCESSFUL.value,
                amount=Decimal("1500.00"),
                currency="NGN",
                idempotency_key="debit-backed-idem-1",
                transaction_id="bill-ref-1",
                provider_status="successful",
                provider_response={"provider": "flutterwave", "status": "successful"},
            )
        ]
        self.refunded_transaction_debit_steps = [self.transaction_debit_step]
        self.exposure_transfers = [self.transfer]
        self.exposure_transaction_debit_steps: list[SimpleNamespace] = []
        self.exposure_direct_transfer_transactions: list[SimpleNamespace] = []
        self.ledger_accounts = _LedgerAccounts()
        self.ledger_entries = _LedgerEntries(self.ledger_accounts)
        self.ledger_reconciliation = _ReconciliationRepo(self)
        self.support_tickets = _SupportTickets()
        self.commit_calls = 0


class _Uow:
    def __init__(self, state: _State) -> None:
        self.db = SimpleNamespace(add=lambda obj: None)
        self.ledger_accounts = state.ledger_accounts
        self.ledger_entries = state.ledger_entries
        self.ledger_reconciliation = state.ledger_reconciliation
        self.funded_transfers = _FundedTransfers({"funded-1": state.transfer})
        self.funding_steps = _FundingSteps([state.step])
        self.transactions = _Transactions(state)
        self.support_tickets = state.support_tickets
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self._state.commit_calls += 1


@pytest.mark.asyncio
async def test_posting_reconciliation_backfills_missing_entries(monkeypatch) -> None:
    state = _State()
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _Uow(state))

    await LedgerPostingReconciliationConsumer().process_job({"limit": 10})

    assert set(state.ledger_entries.entries) == {
        "funding_step:step-1:mono_debit_confirmed",
        "funded_transfer:funded-1:flutterwave_payout_confirmed",
        "funding_step:step-1:mono_refund_confirmed",
        "transaction:tx-direct-1:mono_direct_transfer_confirmed",
        "transaction_debit_step:tx-debit-step-1:mono_debit_confirmed",
        "transaction:tx-bill-1:flutterwave_bill_confirmed",
        "transaction_debit_step:tx-debit-step-1:mono_refund_confirmed",
    }
    assert state.ledger_reconciliation.runs[-1].repaired_count == 7


@pytest.mark.asyncio
async def test_exposure_reconciliation_creates_critical_finding_and_ticket(monkeypatch) -> None:
    state = _State()
    state.ledger_accounts.accounts["liability:funded_transfer:funded-1:NGN"] = SimpleNamespace(
        id="liability:funded_transfer:funded-1:NGN"
    )
    state.ledger_accounts.balance = Decimal("5000.00")
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _Uow(state))

    await LedgerExposureReconciliationConsumer().process_job({"limit": 10, "min_age_seconds": 0})

    finding = state.ledger_reconciliation.findings["ledger_exposure:funded-1"]
    assert finding.severity == "critical"
    assert finding.finding_type == "terminal_non_zero_liability"
    assert finding.actual_amount_naira == Decimal("5000.00")
    assert state.support_tickets.created[0]["intent"] == "ledger_reconciliation"


@pytest.mark.asyncio
async def test_exposure_reconciliation_resolves_zero_balance_finding(monkeypatch) -> None:
    state = _State()
    state.step.status = FundingStepStatusEnum.FAILED.value
    state.ledger_accounts.accounts["liability:funded_transfer:funded-1:NGN"] = SimpleNamespace(
        id="liability:funded_transfer:funded-1:NGN"
    )
    state.ledger_accounts.balance = Decimal("0.00")
    state.ledger_entries.entries["funded_transfer:funded-1:flutterwave_payout_confirmed"] = SimpleNamespace()
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _Uow(state))

    await LedgerExposureReconciliationConsumer().process_job({"limit": 10, "min_age_seconds": 0})

    assert "ledger_exposure:funded-1" in state.ledger_reconciliation.resolved


@pytest.mark.asyncio
async def test_exposure_reconciliation_finds_debit_backed_bill_liability(monkeypatch) -> None:
    state = _State()
    state.exposure_transfers = []
    state.transaction_debit_step.status = TransactionDebitStepStatusEnum.CONFIRMED.value
    state.exposure_transaction_debit_steps = [state.transaction_debit_step]
    state.ledger_accounts.accounts["liability:transaction:tx-bill-1:customer_funds:NGN"] = SimpleNamespace(
        id="liability:transaction:tx-bill-1:customer_funds:NGN"
    )
    state.ledger_accounts.balance = Decimal("1500.00")
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _Uow(state))

    await LedgerExposureReconciliationConsumer().process_job({"limit": 10, "min_age_seconds": 0})

    missing_debit = state.ledger_reconciliation.findings[
        "ledger_missing_entry:transaction_debit_step:tx-debit-step-1:mono_debit_confirmed"
    ]
    exposure = state.ledger_reconciliation.findings["ledger_exposure:transaction:tx-bill-1"]
    assert missing_debit.finding_type == "missing_transaction_debit_ledger_entry"
    assert exposure.severity == "critical"
    assert exposure.finding_type == "terminal_transaction_non_zero_liability"
    assert exposure.actual_amount_naira == Decimal("1500.00")
    assert state.support_tickets.created[0]["intent"] == "ledger_reconciliation"


@pytest.mark.asyncio
async def test_exposure_reconciliation_requires_direct_transfer_entry(monkeypatch) -> None:
    state = _State()
    state.exposure_transfers = []
    state.exposure_direct_transfer_transactions = [
        SimpleNamespace(
            id="tx-direct-missing",
            user_id="user-1",
            transaction_type="transfer",
            status=TransactionStatusEnum.SUCCESSFUL.value,
            amount=Decimal("2500.00"),
            currency="NGN",
            idempotency_key="direct-missing-idem",
            transaction_id="mono-direct-missing",
            provider_status="successful",
            provider_response={"provider": "mono"},
        )
    ]
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _Uow(state))

    await LedgerExposureReconciliationConsumer().process_job({"limit": 10, "min_age_seconds": 0})

    finding = state.ledger_reconciliation.findings[
        "ledger_missing_entry:transaction:tx-direct-missing:mono_direct_transfer_confirmed"
    ]
    assert finding.severity == "critical"
    assert finding.finding_type == "missing_direct_transfer_ledger_entry"
    assert finding.expected_amount_naira == Decimal("2500.00")
