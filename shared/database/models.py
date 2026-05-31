"""SQLAlchemy database models."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from shared.database.enums import (
    BeneficiaryTypeEnum,
    FundedTransferStatusEnum,
    FundingStepStatusEnum,
    MandateStatusEnum,
    RecurrenceTypeEnum,
    ScheduledInstructionStatusEnum,
    ScheduleDomainEnum,
    ScheduledRunStatusEnum,
    SupportTicketPriorityEnum,
    SupportTicketStatusEnum,
    TransactionDebitStepStatusEnum,
    TransactionTypeEnum,
    UserOnboardingStatusEnum,
)
from shared.utils.datetime import utc_now_naive

MONEY_COLUMN = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class User(Base):
    """User database model."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    phone_number: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    mono_customer_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    onboarding_status: Mapped[str | None] = mapped_column(
        String,
        default=UserOnboardingStatusEnum.ONBOARDING_STARTED.value,
        nullable=True,
    )
    last_active: Mapped[datetime | None] = mapped_column(DateTime, default=utc_now_naive)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default={})
    transaction_pin: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    accounts: Mapped[list["Account"]] = relationship("Account", back_populates="user")
    beneficiaries: Mapped[list["Beneficiary"]] = relationship("Beneficiary", back_populates="user")
    transactions: Mapped[list["Transaction"]] = relationship("Transaction", back_populates="user")
    scheduled_instructions: Mapped[list["ScheduledInstruction"]] = relationship(
        "ScheduledInstruction", back_populates="user"
    )
    channel_identities: Mapped[list["UserChannelIdentity"]] = relationship(
        "UserChannelIdentity", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(id={self.id}, phone={self.phone_number}, name={self.full_name})>"


class UserChannelIdentity(Base):
    """Maps a user to a specific messaging channel (WhatsApp, Telegram, etc)."""

    __tablename__ = "user_channel_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_channel_identities_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)  # e.g., "whatsapp", "telegram"
    channel_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)  # e.g., "+234...", "1234567"

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("channel", "channel_user_id", name="uq_user_channel_identity"),)

    user: Mapped["User"] = relationship("User", back_populates="channel_identities")

    def __repr__(self):
        return f"<UserChannelIdentity(user_id={self.user_id}, channel={self.channel}, id={self.channel_user_id})>"


class Account(Base):
    """Bank account database model."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_accounts_user_id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    bank_name: Mapped[str] = mapped_column(String, nullable=False)
    bank_code: Mapped[str | None] = mapped_column(String, nullable=True)
    account_number: Mapped[str] = mapped_column(String, nullable=False)
    account_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    mandate_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    mandate_status: Mapped[str] = mapped_column(String, default=MandateStatusEnum.PENDING.value, nullable=False)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="accounts")

    def __repr__(self):
        return f"<Account(id={self.id}, bank={self.bank_name}, number={self.account_number})>"


class Beneficiary(Base):
    """Beneficiary database model."""

    __tablename__ = "beneficiaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_beneficiaries_user_id"),
        nullable=False,
        index=True,
    )
    beneficiary_type: Mapped[str] = mapped_column(
        String,
        default=BeneficiaryTypeEnum.TRANSFER.value,
        nullable=False,
        index=True,
    )
    account_name: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    bank_code: Mapped[str | None] = mapped_column(String, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="beneficiaries")

    def __repr__(self):
        return (
            f"<Beneficiary(id={self.id}, type={self.beneficiary_type}, "
            f"name={self.account_name}, account={self.account_number})>"
        )


class Transaction(Base):
    """Transaction database model for app-initiated financial actions."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_transactions_user_id"),
        nullable=False,
        index=True,
    )
    transaction_type: Mapped[str] = mapped_column(
        String,
        default=TransactionTypeEnum.TRANSFER.value,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)  # Uses TransactionStatusEnum
    amount: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_transactions_source_account_id"),
        nullable=True,
    )
    source_account_number: Mapped[str] = mapped_column(String, nullable=False)
    source_bank_name: Mapped[str] = mapped_column(String, nullable=False)
    recipient_account_number: Mapped[str | None] = mapped_column(String, nullable=True)
    recipient_bank_code: Mapped[str | None] = mapped_column(String, nullable=True)
    recipient_bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    target_phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    mobile_network: Mapped[str | None] = mapped_column(String, nullable=True)
    biller_code: Mapped[str | None] = mapped_column(String, nullable=True)
    biller_item_code: Mapped[str | None] = mapped_column(String, nullable=True)
    biller_item_name: Mapped[str | None] = mapped_column(String, nullable=True)
    service_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    narration: Mapped[str | None] = mapped_column(String, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    receipt_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    beneficiary_suggested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, status={self.status}, amount={self.amount}, tx_id={self.transaction_id})>"


class RiskDecision(Base):
    """Risk decision recorded before a money-moving transfer is executed."""

    __tablename__ = "risk_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_risk_decisions_user_id"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String, nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False, index=True)
    risk_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_risk_decisions_idempotency_key"),
    )

    def __repr__(self):
        return f"<RiskDecision(idempotency_key={self.idempotency_key}, decision={self.decision})>"


class BankTransaction(Base):
    """Mirrored bank-feed transaction data fetched from providers like Mono."""

    __tablename__ = "bank_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_bank_transactions_user_id"),
        nullable=False,
        index=True,
    )
    linked_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_bank_transactions_linked_account_id"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider_transaction_id: Mapped[str] = mapped_column(String, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    posted_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    transaction_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    counterparty_role: Mapped[str | None] = mapped_column(String, nullable=True)
    counterparty_source: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category_source: Mapped[str | None] = mapped_column(String, nullable=True)
    parser_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "linked_account_id",
            "provider",
            "provider_transaction_id",
            name="uq_bank_transactions_provider_txn",
        ),
    )

    def __repr__(self):
        return (
            f"<BankTransaction(account={self.linked_account_id}, provider={self.provider}, "
            f"provider_tx_id={self.provider_transaction_id})>"
        )


class BankTransactionCoverage(Base):
    """Coverage windows indicating which bank-transaction date ranges are fully mirrored."""

    __tablename__ = "bank_transaction_coverage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    linked_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_bank_transaction_coverage_linked_account_id"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_type: Mapped[str] = mapped_column(String, nullable=False, default="full")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)

    def __repr__(self):
        return (
            f"<BankTransactionCoverage(account={self.linked_account_id}, provider={self.provider}, "
            f"window={self.window_start}..{self.window_end})>"
        )


class FundedTransfer(Base):
    """
    Logical transfer funded from multiple accounts.

    This is the source of truth for multi-account transfers:
    - Multiple debits
    - One payout
    - Transaction-scoped holding balance
    """

    __tablename__ = "funded_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_funded_transfers_user_id"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    recipient_account_number: Mapped[str] = mapped_column(String, nullable=False)
    recipient_bank_code: Mapped[str] = mapped_column(String, nullable=False)
    recipient_bank_name: Mapped[str] = mapped_column(String, nullable=False)
    recipient_name: Mapped[str] = mapped_column(String, nullable=False)
    narration: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(
        String,
        default=FundedTransferStatusEnum.DRAFT.value,
        nullable=False,
        index=True,
    )

    payout_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    payout_reference: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    payout_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_payout_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )
    funding_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payout_initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")
    funding_steps: Mapped[list["FundingStep"]] = relationship(
        "FundingStep", back_populates="funded_transfer", order_by="FundingStep.sequence"
    )

    def __repr__(self):
        return f"<FundedTransfer(id={self.id}, amount={self.amount}, status={self.status})>"


class FundingStep(Base):
    """
    Individual debit from a source account.

    Represents a single direct debit via Mono as part of a FundedTransfer.
    """

    __tablename__ = "funding_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    funded_transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funded_transfers.id", name="fk_funding_steps_funded_transfer_id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_funding_steps_account_id"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, default=FundingStepStatusEnum.PENDING.value, nullable=False, index=True)

    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_debit_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    provider_reference: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    refund_provider_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    refund_provider_reference: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refund_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refund_error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    funded_transfer: Mapped["FundedTransfer"] = relationship("FundedTransfer", back_populates="funding_steps")
    account: Mapped["Account"] = relationship("Account")

    __table_args__ = (
        UniqueConstraint("funded_transfer_id", "sequence", name="uq_funding_steps_transfer_sequence"),
    )

    def __repr__(self):
        return f"<FundingStep(id={self.id}, amount={self.amount}, status={self.status}, seq={self.sequence})>"


class TransactionDebitStep(Base):
    """Single Mono account debit used before bill fulfillment for a normal transaction."""

    __tablename__ = "transaction_debit_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", name="fk_transaction_debit_steps_transaction_id"),
        nullable=False,
        unique=True,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_transaction_debit_steps_account_id"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        default=TransactionDebitStepStatusEnum.PENDING.value,
        nullable=False,
        index=True,
    )

    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_debit_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    provider_reference: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    refund_provider_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    refund_provider_reference: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_initiated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refund_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refund_error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    transaction: Mapped["Transaction"] = relationship("Transaction")
    account: Mapped["Account"] = relationship("Account")

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_debit_steps_positive_amount"),
    )

    def __repr__(self):
        return f"<TransactionDebitStep(id={self.id}, transaction={self.transaction_id}, status={self.status})>"


class LedgerAccount(Base):
    """Double-entry ledger account for confirmed pooled-transfer money movement."""

    __tablename__ = "ledger_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    normal_balance: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False, index=True)
    owner_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_ledger_accounts_user_id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)

    lines: Mapped[list["LedgerLine"]] = relationship("LedgerLine", back_populates="account")

    __table_args__ = (
        CheckConstraint("normal_balance in ('debit', 'credit')", name="ck_ledger_accounts_normal_balance"),
    )


class LedgerEntry(Base):
    """Append-only ledger entry. Corrections are posted as new reversal entries."""

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entry_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    amount_naira: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", name="fk_ledger_entries_transaction_id"),
        nullable=True,
        index=True,
    )
    funded_transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funded_transfers.id", name="fk_ledger_entries_funded_transfer_id"),
        nullable=True,
        index=True,
    )
    funding_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funding_steps.id", name="fk_ledger_entries_funding_step_id"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    provider_reference: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    source_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    entry_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    reversal_of_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_entries.id", name="fk_ledger_entries_reversal_of_entry_id"),
        nullable=True,
        index=True,
    )

    lines: Mapped[list["LedgerLine"]] = relationship(
        "LedgerLine",
        back_populates="entry",
        order_by="LedgerLine.line_number",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("amount_naira > 0", name="ck_ledger_entries_positive_amount"),
    )


class LedgerLine(Base):
    """One side of a ledger entry. Each entry must balance debit and credit lines."""

    __tablename__ = "ledger_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_entries.id", name="fk_ledger_lines_entry_id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_accounts.id", name="fk_ledger_lines_account_id"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    amount_naira: Mapped[Decimal] = mapped_column(MONEY_COLUMN, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    funded_transfer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    funding_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    provider_reference: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)

    entry: Mapped["LedgerEntry"] = relationship("LedgerEntry", back_populates="lines")
    account: Mapped["LedgerAccount"] = relationship("LedgerAccount", back_populates="lines")

    __table_args__ = (
        UniqueConstraint("entry_id", "line_number", name="uq_ledger_lines_entry_line_number"),
        CheckConstraint("direction in ('debit', 'credit')", name="ck_ledger_lines_direction"),
        CheckConstraint("amount_naira > 0", name="ck_ledger_lines_positive_amount"),
        Index("ix_ledger_lines_account_posted_at", "account_id", "posted_at"),
    )


class LedgerReconciliationRun(Base):
    """Execution record for ledger reconciliation scans."""

    __tablename__ = "ledger_reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    run_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repaired_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class LedgerReconciliationFinding(Base):
    """Persistent accounting mismatch or exposure finding."""

    __tablename__ = "ledger_reconciliation_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    finding_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", name="fk_ledger_findings_transaction_id"),
        nullable=True,
        index=True,
    )
    funded_transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funded_transfers.id", name="fk_ledger_findings_funded_transfer_id"),
        nullable=True,
        index=True,
    )
    funding_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("funding_steps.id", name="fk_ledger_findings_funding_step_id"),
        nullable=True,
        index=True,
    )
    support_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support_tickets.id", name="fk_ledger_findings_support_ticket_id"),
        nullable=True,
        index=True,
    )
    expected_amount_naira: Mapped[Decimal | None] = mapped_column(MONEY_COLUMN, nullable=True)
    actual_amount_naira: Mapped[Decimal | None] = mapped_column(MONEY_COLUMN, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProcessedWebhookEvent(Base):
    """Provider webhook event ledger for duplicate delivery protection."""

    __tablename__ = "processed_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    event_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="processing", nullable=False, index=True)
    payload_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_processed_webhook_events_provider_event_id"),
    )

    def __repr__(self):
        return f"<ProcessedWebhookEvent(provider={self.provider}, event={self.event_name}, status={self.status})>"


class ActionableMessage(Base):
    """Actionable message database model for quote-based transaction repeats."""

    __tablename__ = "actionable_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_actionable_messages_user_id"),
        nullable=False,
        index=True,
    )
    channel_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # Uses ActionableMessageTypeEnum
    message_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    user: Mapped["User"] = relationship("User")

    def __repr__(self):
        return f"<ActionableMessage(id={self.id}, channel_msg_id={self.channel_message_id}, type={self.message_type})>"


class ScheduledInstruction(Base):
    """Scheduled instruction for recurring and one-time future transactions."""

    __tablename__ = "scheduled_instructions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_scheduled_instructions_user_id"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String, default=ScheduleDomainEnum.TRANSFER.value, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String, default=ScheduledInstructionStatusEnum.ACTIVE.value, nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default={})
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Africa/Lagos")
    recurrence_type: Mapped[str] = mapped_column(
        String, default=RecurrenceTypeEnum.ONE_TIME.value, nullable=False, index=True
    )
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    local_time: Mapped[str] = mapped_column(String, nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_date: Mapped[str | None] = mapped_column(String, nullable=True)
    next_run_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_run_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="whatsapp")
    channel_identity: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="scheduled_instructions")
    runs: Mapped[list["ScheduledRun"]] = relationship(
        "ScheduledRun", back_populates="schedule", order_by="ScheduledRun.due_at_utc"
    )

    def __repr__(self):
        return (
            f"<ScheduledInstruction(id={self.id}, domain={self.domain}, status={self.status}, "
            f"recurrence={self.recurrence_type})>"
        )


class ScheduledRun(Base):
    """Single execution attempt for a scheduled instruction."""

    __tablename__ = "scheduled_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_instructions.id", name="fk_scheduled_runs_schedule_id"),
        nullable=False,
        index=True,
    )
    due_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default=ScheduledRunStatusEnum.QUEUED.value, nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    schedule: Mapped["ScheduledInstruction"] = relationship("ScheduledInstruction", back_populates="runs")

    def __repr__(self):
        return (
            f"<ScheduledRun(id={self.id}, schedule_id={self.schedule_id}, "
            f"status={self.status}, due={self.due_at_utc})>"
        )


class FAQEntry(Base):
    """FAQ entry for knowledge base retrieval.

    Used by the FAQ/RAG graph to answer informational queries.
    Supports pgvector for semantic search via embeddings.
    """

    __tablename__ = "faq_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=[], nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )

    def __repr__(self):
        return f"<FAQEntry(id={self.id}, category={self.category}, question={self.question[:50]}...)>"


class SupportTicket(Base):
    """Support ticket for tracking user issues.

    Created by the support graph when:
    - User reports fraud
    - Issue requires manual follow-up
    - Max clarification attempts reached
    - Retry/refund fails
    """

    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_support_tickets_user_id"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp", nullable=False)
    intent: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=SupportTicketStatusEnum.OPEN.value, nullable=False, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(10), default=SupportTicketPriorityEnum.MEDIUM.value, nullable=False, index=True
    )

    transaction_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default={}, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")

    def __repr__(self):
        return f"<SupportTicket(id={self.id}, code={self.ticket_code}, status={self.status})>"
