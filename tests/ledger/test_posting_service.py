from decimal import Decimal
from types import SimpleNamespace

import pytest

from banking.ledger.errors import LedgerEntryConflict, LedgerEntryUnbalanced
from banking.ledger.posting import LedgerPostingService, transaction_success_entry_key
from banking.ledger.repositories.ledger_entry_repository import LedgerLineInput


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
        self.lines: dict[str, tuple[LedgerLineInput, ...]] = {}

    async def get_by_key(self, entry_key: str) -> SimpleNamespace | None:
        return self.entries.get(entry_key)

    async def create_entry_with_lines(self, **kwargs) -> SimpleNamespace:
        entry = SimpleNamespace(id=kwargs["entry_key"], **kwargs)
        self.entries[kwargs["entry_key"]] = entry
        self.lines[kwargs["entry_key"]] = tuple(kwargs["lines"])
        return entry


class _Transactions:
    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=f"tx:{idempotency_key}")


class _Uow:
    def __init__(self) -> None:
        self.ledger_accounts = _LedgerAccounts()
        self.ledger_entries = _LedgerEntries()
        self.transactions = _Transactions()


def _transfer() -> SimpleNamespace:
    return SimpleNamespace(
        id="funded-1",
        user_id="user-1",
        amount=Decimal("5000.00"),
        currency="NGN",
        idempotency_key="idem-1",
    )


def _step() -> SimpleNamespace:
    return SimpleNamespace(
        id="step-1",
        funded_transfer_id="funded-1",
        amount=Decimal("2500.00"),
        provider_debit_id="debit-1",
        provider_reference="mono-ref-1",
        refund_provider_id="refund-1",
        refund_provider_reference="refund-ref-1",
    )


def _transaction(transaction_type: str = "transfer") -> SimpleNamespace:
    provider_response = {"provider": "mono"} if transaction_type == "transfer" else {"provider": "flutterwave"}
    return SimpleNamespace(
        id="tx-1",
        user_id="user-1",
        transaction_type=transaction_type,
        amount=Decimal("1500.00"),
        currency="NGN",
        idempotency_key=f"idem-{transaction_type}-1",
        transaction_id=f"provider-{transaction_type}-1",
        provider_status="successful",
        provider_response=provider_response,
    )


@pytest.mark.asyncio
async def test_mono_funding_confirmed_posts_balanced_lines() -> None:
    uow = _Uow()
    transfer = _transfer()
    step = _step()

    entry = await LedgerPostingService.post_mono_funding_confirmed(
        uow,  # type: ignore[arg-type]
        step,
        transfer,
        provider_reference="mono-ref-1",
    )

    assert entry.entry_key == "funding_step:step-1:mono_debit_confirmed"
    assert entry.entry_type == "mono_debit_confirmed"
    lines = uow.ledger_entries.lines[entry.entry_key]
    assert [(line.account_id, line.direction, line.amount_naira) for line in lines] == [
        ("asset:mono:funding_receivable:NGN", "debit", Decimal("2500.00")),
        ("liability:funded_transfer:funded-1:NGN", "credit", Decimal("2500.00")),
    ]


@pytest.mark.asyncio
async def test_duplicate_funding_post_returns_existing_entry() -> None:
    uow = _Uow()
    transfer = _transfer()
    step = _step()

    first = await LedgerPostingService.post_mono_funding_confirmed(
        uow,  # type: ignore[arg-type]
        step,
        transfer,
        provider_reference="mono-ref-1",
    )
    second = await LedgerPostingService.post_mono_funding_confirmed(
        uow,  # type: ignore[arg-type]
        step,
        transfer,
        provider_reference="mono-ref-1",
    )

    assert second is first
    assert len(uow.ledger_entries.entries) == 1


@pytest.mark.asyncio
async def test_duplicate_key_with_different_amount_raises_conflict() -> None:
    uow = _Uow()
    transfer = _transfer()
    step = _step()
    uow.ledger_entries.entries["funding_step:step-1:mono_debit_confirmed"] = SimpleNamespace(
        entry_key="funding_step:step-1:mono_debit_confirmed",
        entry_type="mono_debit_confirmed",
        amount_naira=Decimal("2000.00"),
        currency="NGN",
    )

    with pytest.raises(LedgerEntryConflict):
        await LedgerPostingService.post_mono_funding_confirmed(
            uow,  # type: ignore[arg-type]
            step,
            transfer,
            provider_reference="mono-ref-1",
        )


@pytest.mark.asyncio
async def test_flutterwave_payout_confirmed_debits_liability_and_credits_settlement() -> None:
    uow = _Uow()
    transfer = _transfer()

    entry = await LedgerPostingService.post_flutterwave_payout_confirmed(
        uow,  # type: ignore[arg-type]
        transfer,
        provider_reference="trf-1",
        result={"status": "successful", "amount_naira": Decimal("5000.00")},
    )

    lines = uow.ledger_entries.lines[entry.entry_key]
    assert [(line.account_id, line.direction, line.amount_naira) for line in lines] == [
        ("liability:funded_transfer:funded-1:NGN", "debit", Decimal("5000.00")),
        ("asset:flutterwave:payout_settlement:NGN", "credit", Decimal("5000.00")),
    ]


@pytest.mark.asyncio
async def test_mono_refund_confirmed_debits_liability_and_credits_receivable() -> None:
    uow = _Uow()
    transfer = _transfer()
    step = _step()

    entry = await LedgerPostingService.post_mono_refund_confirmed(
        uow,  # type: ignore[arg-type]
        step,
        transfer,
        provider_reference="refund-ref-1",
    )

    lines = uow.ledger_entries.lines[entry.entry_key]
    assert [(line.account_id, line.direction, line.amount_naira) for line in lines] == [
        ("liability:funded_transfer:funded-1:NGN", "debit", Decimal("2500.00")),
        ("asset:mono:funding_receivable:NGN", "credit", Decimal("2500.00")),
    ]


def test_unbalanced_lines_are_rejected() -> None:
    with pytest.raises(LedgerEntryUnbalanced):
        LedgerPostingService._validate_lines(
            (
                LedgerLineInput(account_id="a", direction="debit", amount_naira=Decimal("10.00")),
                LedgerLineInput(account_id="b", direction="credit", amount_naira=Decimal("9.00")),
            )
        )


@pytest.mark.asyncio
async def test_direct_transfer_success_posts_transaction_level_clearing_entry() -> None:
    uow = _Uow()
    transaction = _transaction("transfer")

    entry = await LedgerPostingService.post_transaction_success_confirmed(
        uow,  # type: ignore[arg-type]
        transaction,
    )

    assert entry.entry_key == transaction_success_entry_key("tx-1", "transfer")
    assert entry.entry_type == "mono_direct_transfer_confirmed"
    lines = uow.ledger_entries.lines[entry.entry_key]
    assert [(line.account_id, line.direction, line.amount_naira) for line in lines] == [
        ("clearing:mono:direct_transfer_destination:NGN", "debit", Decimal("1500.00")),
        ("clearing:mono:direct_transfer_source:NGN", "credit", Decimal("1500.00")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transaction_type", "entry_type", "debit_account"),
    [
        ("airtime", "airtime_purchase_confirmed", "expense:flutterwave:airtime_fulfillment:NGN"),
        ("data", "data_purchase_confirmed", "expense:flutterwave:data_fulfillment:NGN"),
        ("bill", "bill_payment_confirmed", "expense:flutterwave:bill_fulfillment:NGN"),
    ],
)
async def test_bill_success_posts_provider_fulfillment_entry(
    transaction_type: str,
    entry_type: str,
    debit_account: str,
) -> None:
    uow = _Uow()
    transaction = _transaction(transaction_type)

    entry = await LedgerPostingService.post_transaction_success_confirmed(
        uow,  # type: ignore[arg-type]
        transaction,
    )

    assert entry.entry_key == transaction_success_entry_key("tx-1", transaction_type)
    assert entry.entry_type == entry_type
    lines = uow.ledger_entries.lines[entry.entry_key]
    assert [(line.account_id, line.direction, line.amount_naira) for line in lines] == [
        (debit_account, "debit", Decimal("1500.00")),
        ("asset:flutterwave:bill_settlement:NGN", "credit", Decimal("1500.00")),
    ]
