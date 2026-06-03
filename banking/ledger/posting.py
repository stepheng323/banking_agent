"""Double-entry posting service for confirmed financial events."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol, TypeAlias
from uuid import UUID

from banking.ledger.errors import LedgerEntryConflict, LedgerEntryUnbalanced
from banking.ledger.repositories.ledger_account_repository import LedgerAccountRepository
from banking.ledger.repositories.ledger_entry_repository import LedgerEntryRepository, LedgerLineInput
from shared.database.models import LedgerAccount, LedgerEntry
from shared.money import require_naira
from shared.utils.json import to_json_safe_dict

FUNDING_ENTRY_TYPE = "mono_debit_confirmed"
PAYOUT_ENTRY_TYPE = "flutterwave_payout_confirmed"
REFUND_ENTRY_TYPE = "mono_refund_confirmed"
TRANSACTION_DEBIT_ENTRY_TYPE = "mono_transaction_debit_confirmed"
TRANSACTION_BILL_ENTRY_TYPE = "flutterwave_bill_confirmed"
TRANSACTION_DEBIT_REFUND_ENTRY_TYPE = "mono_transaction_debit_refund_confirmed"
DIRECT_TRANSFER_ENTRY_TYPE = "mono_direct_transfer_confirmed"
AIRTIME_ENTRY_TYPE = "airtime_purchase_confirmed"
DATA_ENTRY_TYPE = "data_purchase_confirmed"
BILL_ENTRY_TYPE = "bill_payment_confirmed"
LEDGER_CURRENCY = "NGN"
LedgerId: TypeAlias = UUID | str


class LedgerPostingUnitOfWork(Protocol):
    """Repository surface required by ledger posting."""

    @property
    def ledger_accounts(self) -> LedgerAccountRepository | None: ...

    @property
    def ledger_entries(self) -> LedgerEntryRepository | None: ...

    @property
    def transactions(self) -> Any | None: ...


class FundingStepLedgerSource(Protocol):
    """Funding-step fields required to post pooled-transfer ledger entries."""

    @property
    def id(self) -> LedgerId: ...

    @property
    def amount(self) -> object: ...


class FundedTransferLedgerSource(Protocol):
    """Funded-transfer fields required to post pooled-transfer ledger entries."""

    @property
    def id(self) -> LedgerId: ...

    @property
    def amount(self) -> object: ...

    @property
    def user_id(self) -> LedgerId: ...

    @property
    def idempotency_key(self) -> str: ...


class TransactionLedgerSource(Protocol):
    """Transaction fields required to post transaction-level ledger entries."""

    @property
    def id(self) -> LedgerId: ...

    @property
    def transaction_type(self) -> str: ...

    @property
    def amount(self) -> object: ...

    @property
    def currency(self) -> str: ...

    @property
    def user_id(self) -> LedgerId: ...

    @property
    def idempotency_key(self) -> str: ...

    @property
    def transaction_id(self) -> str | None: ...

    @property
    def provider_status(self) -> str | None: ...

    @property
    def provider_response(self) -> Mapping[str, Any] | None: ...


class TransactionDebitStepLedgerSource(Protocol):
    """Transaction debit-step fields required to post single-transaction ledger entries."""

    @property
    def id(self) -> LedgerId: ...

    @property
    def transaction_id(self) -> LedgerId: ...

    @property
    def account_id(self) -> LedgerId: ...

    @property
    def amount(self) -> object: ...

    @property
    def provider_debit_id(self) -> str | None: ...

    @property
    def provider_reference(self) -> str | None: ...

    @property
    def refund_provider_id(self) -> str | None: ...

    @property
    def refund_provider_reference(self) -> str | None: ...


def transaction_success_entry_type(transaction_type: str) -> str:
    """Return the ledger entry type for a successful non-pooled transaction."""
    normalized = str(transaction_type or "").strip().lower()
    if normalized == "transfer":
        return DIRECT_TRANSFER_ENTRY_TYPE
    if normalized == "airtime":
        return AIRTIME_ENTRY_TYPE
    if normalized == "data":
        return DATA_ENTRY_TYPE
    if normalized == "bill":
        return BILL_ENTRY_TYPE
    raise ValueError(f"Unsupported transaction type for ledger posting: {transaction_type}")


def transaction_success_entry_key(transaction_id: LedgerId, transaction_type: str) -> str:
    """Return the deterministic ledger key for a successful non-pooled transaction."""
    return f"transaction:{transaction_id}:{transaction_success_entry_type(transaction_type)}"


class LedgerPostingService:
    """Posts immutable, balanced ledger entries for confirmed money movement."""

    @classmethod
    async def post_mono_funding_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        funding_step: FundingStepLedgerSource,
        transfer: FundedTransferLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
    ) -> LedgerEntry:
        """Post confirmed Mono funding debit into pooled transfer liability."""
        amount = require_naira(getattr(funding_step, "amount", None))
        mono_account, liability_account = await cls._funding_accounts(uow, transfer)
        entry_key = f"funding_step:{funding_step.id}:mono_debit_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=FUNDING_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=mono_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=liability_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=await cls._transaction_id_for_transfer(uow, transfer),
            funded_transfer_id=transfer.id,
            funding_step_id=funding_step.id,
            provider="mono",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="funding_step",
            source_id=str(funding_step.id),
            metadata={
                "funded_transfer_id": str(transfer.id),
                "funding_step_id": str(funding_step.id),
                "provider_debit_id": getattr(funding_step, "provider_debit_id", None),
            },
        )

    @classmethod
    async def post_flutterwave_payout_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        transfer: FundedTransferLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> LedgerEntry:
        """Post confirmed Flutterwave payout from pooled transfer liability."""
        amount = require_naira(getattr(transfer, "amount", None))
        liability_account = await cls._liability_account(uow, transfer)
        flutterwave_account = await cls._flutterwave_payout_settlement_account(uow)
        entry_key = f"funded_transfer:{transfer.id}:flutterwave_payout_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=PAYOUT_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=liability_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=flutterwave_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=await cls._transaction_id_for_transfer(uow, transfer),
            funded_transfer_id=transfer.id,
            funding_step_id=None,
            provider="flutterwave",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="funded_transfer",
            source_id=str(transfer.id),
            metadata={
                "funded_transfer_id": str(transfer.id),
                "provider_result": to_json_safe_dict(result or {}),
            },
        )

    @classmethod
    async def post_mono_refund_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        funding_step: FundingStepLedgerSource,
        transfer: FundedTransferLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
    ) -> LedgerEntry:
        """Post confirmed Mono refund that reverses user-money exposure."""
        amount = require_naira(getattr(funding_step, "amount", None))
        liability_account = await cls._liability_account(uow, transfer)
        mono_account = await cls._mono_funding_receivable_account(uow)
        entry_key = f"funding_step:{funding_step.id}:mono_refund_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=REFUND_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=liability_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=mono_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=await cls._transaction_id_for_transfer(uow, transfer),
            funded_transfer_id=transfer.id,
            funding_step_id=funding_step.id,
            provider="mono",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="funding_step",
            source_id=str(funding_step.id),
            metadata={
                "funded_transfer_id": str(transfer.id),
                "funding_step_id": str(funding_step.id),
                "refund_provider_id": getattr(funding_step, "refund_provider_id", None),
            },
        )

    @classmethod
    async def post_transaction_debit_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        debit_step: TransactionDebitStepLedgerSource,
        transaction: TransactionLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
    ) -> LedgerEntry:
        """Post confirmed Mono debit into a transaction customer-funds liability."""
        amount = require_naira(getattr(debit_step, "amount", None))
        mono_account = await cls._mono_transaction_funding_receivable_account(uow)
        liability_account = await cls._transaction_liability_account(uow, transaction)
        entry_key = f"transaction_debit_step:{debit_step.id}:mono_debit_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=TRANSACTION_DEBIT_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=mono_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=liability_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=transaction.id,
            funded_transfer_id=None,
            funding_step_id=None,
            provider="mono",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="transaction_debit_step",
            source_id=str(debit_step.id),
            metadata={
                "transaction_id": str(transaction.id),
                "transaction_debit_step_id": str(debit_step.id),
                "provider_debit_id": getattr(debit_step, "provider_debit_id", None),
            },
        )

    @classmethod
    async def post_transaction_bill_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        transaction: TransactionLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> LedgerEntry:
        """Post confirmed Flutterwave bill fulfillment from transaction customer funds."""
        amount = require_naira(getattr(transaction, "amount", None))
        liability_account = await cls._transaction_liability_account(uow, transaction)
        flutterwave_account = await cls._bill_settlement_account(uow, "flutterwave")
        entry_key = f"transaction:{transaction.id}:flutterwave_bill_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=TRANSACTION_BILL_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=liability_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=flutterwave_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=transaction.id,
            funded_transfer_id=None,
            funding_step_id=None,
            provider="flutterwave",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="transaction",
            source_id=str(transaction.id),
            metadata={
                "transaction_id": str(transaction.id),
                "transaction_type": str(getattr(transaction, "transaction_type", "")),
                "provider_result": to_json_safe_dict(result or {}),
            },
        )

    @classmethod
    async def post_transaction_debit_refund_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        debit_step: TransactionDebitStepLedgerSource,
        transaction: TransactionLedgerSource,
        *,
        provider_reference: str | None,
        provider_event_id: str | None = None,
    ) -> LedgerEntry:
        """Post confirmed Mono refund for a transaction debit."""
        amount = require_naira(getattr(debit_step, "amount", None))
        liability_account = await cls._transaction_liability_account(uow, transaction)
        mono_account = await cls._mono_transaction_funding_receivable_account(uow)
        entry_key = f"transaction_debit_step:{debit_step.id}:mono_refund_confirmed"
        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=TRANSACTION_DEBIT_REFUND_ENTRY_TYPE,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=liability_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=mono_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=transaction.id,
            funded_transfer_id=None,
            funding_step_id=None,
            provider="mono",
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type="transaction_debit_step",
            source_id=str(debit_step.id),
            metadata={
                "transaction_id": str(transaction.id),
                "transaction_debit_step_id": str(debit_step.id),
                "refund_provider_id": getattr(debit_step, "refund_provider_id", None),
            },
        )

    @classmethod
    async def post_transaction_success_confirmed(
        cls,
        uow: LedgerPostingUnitOfWork,
        transaction: TransactionLedgerSource,
        *,
        provider_reference: str | None = None,
        provider_event_id: str | None = None,
    ) -> LedgerEntry:
        """Post confirmed success for a non-pooled transaction row.

        Pooled transfers are posted through funding/payout/refund entries instead.
        This method covers direct Mono transfers and provider bill fulfillment.
        """
        amount = require_naira(getattr(transaction, "amount", None))
        transaction_type = str(getattr(transaction, "transaction_type", "") or "").strip().lower()
        provider = cls._provider_for_transaction(transaction)
        debit_account, credit_account = await cls._transaction_success_accounts(
            uow,
            transaction_type=transaction_type,
            provider=provider,
        )
        entry_type = transaction_success_entry_type(transaction_type)
        entry_key = transaction_success_entry_key(transaction.id, transaction_type)
        reference = provider_reference or getattr(transaction, "transaction_id", None) or transaction.idempotency_key

        return await cls._post_entry(
            uow,
            entry_key=entry_key,
            entry_type=entry_type,
            amount_naira=amount,
            lines=(
                LedgerLineInput(account_id=debit_account.id, direction="debit", amount_naira=amount),
                LedgerLineInput(account_id=credit_account.id, direction="credit", amount_naira=amount),
            ),
            transaction_id=transaction.id,
            funded_transfer_id=None,
            funding_step_id=None,
            provider=provider,
            provider_reference=reference,
            provider_event_id=provider_event_id,
            source_type="transaction",
            source_id=str(transaction.id),
            metadata={
                "transaction_type": transaction_type,
                "idempotency_key": transaction.idempotency_key,
                "provider_status": getattr(transaction, "provider_status", None),
                "provider": provider,
            },
        )

    @classmethod
    async def _post_entry(
        cls,
        uow: LedgerPostingUnitOfWork,
        *,
        entry_key: str,
        entry_type: str,
        amount_naira: Decimal,
        lines: tuple[LedgerLineInput, ...],
        transaction_id: LedgerId | None,
        funded_transfer_id: LedgerId | None,
        funding_step_id: LedgerId | None,
        provider: str,
        provider_reference: str | None,
        provider_event_id: str | None,
        source_type: str,
        source_id: str,
        metadata: dict[str, Any],
    ) -> LedgerEntry:
        entries = cls._entries(uow)
        amount = require_naira(amount_naira)
        cls._validate_lines(lines)
        existing = await entries.get_by_key(entry_key)
        if existing:
            cls._raise_if_conflicting(existing, entry_type=entry_type, amount_naira=amount, currency=LEDGER_CURRENCY)
            return existing

        return await entries.create_entry_with_lines(
            entry_key=entry_key,
            entry_type=entry_type,
            amount_naira=amount,
            currency=LEDGER_CURRENCY,
            lines=lines,
            transaction_id=transaction_id,
            funded_transfer_id=funded_transfer_id,
            funding_step_id=funding_step_id,
            provider=provider,
            provider_reference=provider_reference,
            provider_event_id=provider_event_id,
            source_type=source_type,
            source_id=source_id,
            metadata=to_json_safe_dict(metadata),
        )

    @staticmethod
    def _validate_lines(lines: tuple[LedgerLineInput, ...]) -> None:
        if not lines:
            raise LedgerEntryUnbalanced("Ledger entry requires at least one debit and one credit line")
        debits = Decimal("0.00")
        credits = Decimal("0.00")
        for line in lines:
            amount = require_naira(line.amount_naira)
            if amount <= 0:
                raise ValueError("Ledger line amount must be positive")
            if line.direction == "debit":
                debits += amount
            elif line.direction == "credit":
                credits += amount
            else:
                raise ValueError("Ledger line direction must be debit or credit")
        if debits != credits:
            raise LedgerEntryUnbalanced(f"Ledger entry is unbalanced: debit={debits} credit={credits}")

    @staticmethod
    def _raise_if_conflicting(
        existing: LedgerEntry,
        *,
        entry_type: str,
        amount_naira: Decimal,
        currency: str,
    ) -> None:
        existing_amount = require_naira(existing.amount_naira)
        amount = require_naira(amount_naira)
        if existing.entry_type != entry_type or existing_amount != amount or existing.currency != currency:
            raise LedgerEntryConflict(
                f"Ledger entry key exists with different accounting details: entry_key={existing.entry_key}"
            )

    @classmethod
    async def _funding_accounts(
        cls,
        uow: LedgerPostingUnitOfWork,
        transfer: FundedTransferLedgerSource,
    ) -> tuple[LedgerAccount, LedgerAccount]:
        return await cls._mono_funding_receivable_account(uow), await cls._liability_account(uow, transfer)

    @classmethod
    async def _mono_funding_receivable_account(cls, uow: LedgerPostingUnitOfWork) -> LedgerAccount:
        accounts = cls._accounts(uow)
        return await accounts.get_or_create(
            code="asset:mono:funding_receivable:NGN",
            name="Mono funding receivable",
            account_type="asset",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider="mono",
        )

    @classmethod
    async def _mono_transaction_funding_receivable_account(cls, uow: LedgerPostingUnitOfWork) -> LedgerAccount:
        accounts = cls._accounts(uow)
        return await accounts.get_or_create(
            code="asset:mono:transaction_funding_receivable:NGN",
            name="Mono transaction funding receivable",
            account_type="asset",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider="mono",
        )

    @classmethod
    async def _flutterwave_payout_settlement_account(cls, uow: LedgerPostingUnitOfWork) -> LedgerAccount:
        accounts = cls._accounts(uow)
        return await accounts.get_or_create(
            code="asset:flutterwave:payout_settlement:NGN",
            name="Flutterwave payout settlement",
            account_type="asset",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider="flutterwave",
        )

    @classmethod
    async def _transaction_liability_account(
        cls,
        uow: LedgerPostingUnitOfWork,
        transaction: TransactionLedgerSource,
    ) -> LedgerAccount:
        accounts = cls._accounts(uow)
        return await accounts.get_or_create(
            code=f"liability:transaction:{transaction.id}:customer_funds:NGN",
            name=f"Transaction customer funds liability {transaction.id}",
            account_type="liability",
            normal_balance="credit",
            currency=LEDGER_CURRENCY,
            owner_type="transaction",
            owner_id=transaction.id,
            user_id=getattr(transaction, "user_id", None),
        )

    @classmethod
    async def _liability_account(
        cls,
        uow: LedgerPostingUnitOfWork,
        transfer: FundedTransferLedgerSource,
    ) -> LedgerAccount:
        accounts = cls._accounts(uow)
        return await accounts.get_or_create(
            code=f"liability:funded_transfer:{transfer.id}:NGN",
            name=f"Funded transfer liability {transfer.id}",
            account_type="liability",
            normal_balance="credit",
            currency=LEDGER_CURRENCY,
            owner_type="funded_transfer",
            owner_id=transfer.id,
            user_id=getattr(transfer, "user_id", None),
        )

    @classmethod
    async def _transaction_success_accounts(
        cls,
        uow: LedgerPostingUnitOfWork,
        *,
        transaction_type: str,
        provider: str,
    ) -> tuple[LedgerAccount, LedgerAccount]:
        if transaction_type == "transfer":
            destination_account = await cls._direct_transfer_destination_account(uow, provider)
            source_account = await cls._direct_transfer_source_account(uow, provider)
            return destination_account, source_account
        if transaction_type in {"airtime", "data", "bill"}:
            return await cls._bill_fulfillment_account(
                uow,
                provider=provider,
                transaction_type=transaction_type,
            ), await cls._bill_settlement_account(uow, provider)
        raise ValueError(f"Unsupported transaction type for ledger posting: {transaction_type}")

    @classmethod
    async def _direct_transfer_source_account(
        cls,
        uow: LedgerPostingUnitOfWork,
        provider: str,
    ) -> LedgerAccount:
        accounts = cls._accounts(uow)
        provider_code = cls._account_code_component(provider)
        return await accounts.get_or_create(
            code=f"clearing:{provider_code}:direct_transfer_source:NGN",
            name=f"{provider.title()} direct transfer source clearing",
            account_type="clearing",
            normal_balance="credit",
            currency=LEDGER_CURRENCY,
            provider=provider_code,
        )

    @classmethod
    async def _direct_transfer_destination_account(
        cls,
        uow: LedgerPostingUnitOfWork,
        provider: str,
    ) -> LedgerAccount:
        accounts = cls._accounts(uow)
        provider_code = cls._account_code_component(provider)
        return await accounts.get_or_create(
            code=f"clearing:{provider_code}:direct_transfer_destination:NGN",
            name=f"{provider.title()} direct transfer destination clearing",
            account_type="clearing",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider=provider_code,
        )

    @classmethod
    async def _bill_settlement_account(cls, uow: LedgerPostingUnitOfWork, provider: str) -> LedgerAccount:
        accounts = cls._accounts(uow)
        provider_code = cls._account_code_component(provider)
        return await accounts.get_or_create(
            code=f"asset:{provider_code}:bill_settlement:NGN",
            name=f"{provider.title()} bill settlement",
            account_type="asset",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider=provider_code,
        )

    @classmethod
    async def _bill_fulfillment_account(
        cls,
        uow: LedgerPostingUnitOfWork,
        *,
        provider: str,
        transaction_type: str,
    ) -> LedgerAccount:
        accounts = cls._accounts(uow)
        provider_code = cls._account_code_component(provider)
        product_code = cls._account_code_component(transaction_type)
        return await accounts.get_or_create(
            code=f"expense:{provider_code}:{product_code}_fulfillment:NGN",
            name=f"{provider.title()} {transaction_type} fulfillment",
            account_type="expense",
            normal_balance="debit",
            currency=LEDGER_CURRENCY,
            provider=provider_code,
        )

    @staticmethod
    def _provider_for_transaction(transaction: TransactionLedgerSource) -> str:
        response = getattr(transaction, "provider_response", None)
        if isinstance(response, Mapping):
            provider = response.get("provider")
            if provider:
                return LedgerPostingService._account_code_component(str(provider))
        transaction_type = str(getattr(transaction, "transaction_type", "") or "").strip().lower()
        if transaction_type == "transfer":
            return "mono"
        if transaction_type in {"airtime", "data", "bill"}:
            return "flutterwave"
        return "unknown"

    @staticmethod
    def _account_code_component(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
        return "_".join(part for part in cleaned.split("_") if part) or "unknown"

    @staticmethod
    async def _transaction_id_for_transfer(
        uow: LedgerPostingUnitOfWork,
        transfer: FundedTransferLedgerSource,
    ) -> UUID | None:
        transactions = getattr(uow, "transactions", None)
        if not transactions:
            return None
        tx = await transactions.get_by_idempotency_key(str(transfer.idempotency_key))
        if not tx:
            return None
        tx_id = getattr(tx, "id", None)
        if isinstance(tx_id, UUID):
            return tx_id
        try:
            return UUID(str(tx_id))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _accounts(uow: LedgerPostingUnitOfWork) -> LedgerAccountRepository:
        if not uow.ledger_accounts:
            raise RuntimeError("ledger_account_repository_unavailable")
        return uow.ledger_accounts

    @staticmethod
    def _entries(uow: LedgerPostingUnitOfWork) -> LedgerEntryRepository:
        if not uow.ledger_entries:
            raise RuntimeError("ledger_entry_repository_unavailable")
        return uow.ledger_entries
