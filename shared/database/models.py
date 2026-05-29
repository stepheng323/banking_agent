"""SQLAlchemy database models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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
    TransactionTypeEnum,
    UserOnboardingStatusEnum,
)
from shared.utils.datetime import utc_now_naive


class Base(DeclarativeBase):
    pass


class User(Base):
    """User database model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    address = Column(String, nullable=True)
    mono_customer_id = Column(String, nullable=True, index=True)

    onboarding_status = Column(
        String,
        default=UserOnboardingStatusEnum.ONBOARDING_STARTED.value,
        nullable=True,
    )
    last_active = Column(DateTime, default=utc_now_naive)
    extra_data = Column(JSON, default={})
    transaction_pin = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)

    accounts = relationship("Account", back_populates="user")
    beneficiaries = relationship("Beneficiary", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    scheduled_instructions = relationship("ScheduledInstruction", back_populates="user")
    channel_identities = relationship("UserChannelIdentity", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, phone={self.phone_number}, name={self.full_name})>"


class UserChannelIdentity(Base):
    """Maps a user to a specific messaging channel (WhatsApp, Telegram, etc)."""

    __tablename__ = "user_channel_identities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_channel_identities_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(String, nullable=False)  # e.g., "whatsapp", "telegram"
    channel_user_id = Column(String, nullable=False, index=True)  # e.g., "+234...", "1234567"

    created_at = Column(DateTime, server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("channel", "channel_user_id", name="uq_user_channel_identity"),)

    user = relationship("User", back_populates="channel_identities")

    def __repr__(self):
        return f"<UserChannelIdentity(user_id={self.user_id}, channel={self.channel}, id={self.channel_user_id})>"


class Account(Base):
    """Bank account database model."""

    __tablename__ = "accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_accounts_user_id"),
        nullable=False,
        index=True,
    )
    account_id = Column(String, nullable=False, unique=True)
    bank_name = Column(String, nullable=False)
    bank_code = Column(String, nullable=True)
    account_number = Column(String, nullable=False)
    account_name = Column(String, nullable=True)
    is_default = Column(Boolean, default=False)

    mandate_id = Column(String, nullable=True, index=True)
    mandate_status = Column(String, default=MandateStatusEnum.PENDING.value, nullable=False)
    extra_data = Column(JSON, default={})
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)

    user = relationship("User", back_populates="accounts")

    def __repr__(self):
        return f"<Account(id={self.id}, bank={self.bank_name}, number={self.account_number})>"


class Beneficiary(Base):
    """Beneficiary database model."""

    __tablename__ = "beneficiaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_beneficiaries_user_id"),
        nullable=False,
        index=True,
    )
    beneficiary_type = Column(
        String,
        default=BeneficiaryTypeEnum.TRANSFER.value,
        nullable=False,
        index=True,
    )
    account_name = Column(String, nullable=False)
    alias = Column(String, nullable=True)
    account_number = Column(String, nullable=True)
    bank_code = Column(String, nullable=True)
    bank_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)

    user = relationship("User", back_populates="beneficiaries")

    def __repr__(self):
        return (
            f"<Beneficiary(id={self.id}, type={self.beneficiary_type}, "
            f"name={self.account_name}, account={self.account_number})>"
        )


class Transaction(Base):
    """Transaction database model for app-initiated financial actions."""

    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_transactions_user_id"),
        nullable=False,
        index=True,
    )
    transaction_type = Column(
        String,
        default=TransactionTypeEnum.TRANSFER.value,
        nullable=False,
    )
    status = Column(String, nullable=False, index=True)  # Uses TransactionStatusEnum
    amount = Column(Float, nullable=False)
    currency = Column(String, default="NGN", nullable=False)
    source_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_transactions_source_account_id"),
        nullable=True,
    )
    source_account_number = Column(String, nullable=False)
    source_bank_name = Column(String, nullable=False)
    recipient_account_number = Column(String, nullable=True)
    recipient_bank_code = Column(String, nullable=True)
    recipient_bank_name = Column(String, nullable=True)
    recipient_name = Column(String, nullable=True)
    target_phone_number = Column(String, nullable=True)
    mobile_network = Column(String, nullable=True)
    biller_code = Column(String, nullable=True)
    biller_item_code = Column(String, nullable=True)
    biller_item_name = Column(String, nullable=True)
    service_metadata = Column(JSON, nullable=True)
    narration = Column(String, nullable=True)
    transaction_id = Column(String, nullable=True)
    idempotency_key = Column(String, unique=True, nullable=False, index=True)
    error_message = Column(String, nullable=True)
    provider_response = Column(JSON, nullable=True)
    provider_status = Column(String, nullable=True)
    provider_error_code = Column(String, nullable=True)
    receipt_sent = Column(Boolean, default=False, nullable=False)
    beneficiary_suggested = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, status={self.status}, amount={self.amount}, tx_id={self.transaction_id})>"


class BankTransaction(Base):
    """Mirrored bank-feed transaction data fetched from providers like Mono."""

    __tablename__ = "bank_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_bank_transactions_user_id"),
        nullable=False,
        index=True,
    )
    linked_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_bank_transactions_linked_account_id"),
        nullable=False,
        index=True,
    )
    provider = Column(String, nullable=False, index=True)
    provider_transaction_id = Column(String, nullable=False)
    posted_at = Column(DateTime, nullable=False, index=True)
    posted_date = Column(Date, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="NGN", nullable=False)
    transaction_type = Column(String, nullable=False, index=True)
    narration = Column(Text, nullable=True)
    category = Column(String, nullable=True)
    counterparty = Column(String, nullable=True, index=True)
    counterparty_role = Column(String, nullable=True)
    counterparty_source = Column(String, nullable=True)
    resolved_category = Column(String, nullable=True, index=True)
    category_source = Column(String, nullable=True)
    parser_rule = Column(String, nullable=True)
    bank_name = Column(String, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime, server_default=text("now()"), nullable=False)
    last_seen_at = Column(DateTime, server_default=text("now()"), nullable=False)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    linked_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_bank_transaction_coverage_linked_account_id"),
        nullable=False,
        index=True,
    )
    provider = Column(String, nullable=False, index=True)
    window_start = Column(Date, nullable=False)
    window_end = Column(Date, nullable=False)
    coverage_type = Column(String, nullable=False, default="full")
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_funded_transfers_user_id"),
        nullable=False,
        index=True,
    )

    amount = Column(Float, nullable=False)
    currency = Column(String, default="NGN", nullable=False)
    recipient_account_number = Column(String, nullable=False)
    recipient_bank_code = Column(String, nullable=False)
    recipient_bank_name = Column(String, nullable=False)
    recipient_name = Column(String, nullable=False)
    narration = Column(String, nullable=True)

    status = Column(String, default=FundedTransferStatusEnum.DRAFT.value, nullable=False, index=True)

    payout_provider = Column(String, nullable=True)
    payout_reference = Column(String, nullable=True, index=True)
    idempotency_key = Column(String, unique=True, nullable=False, index=True)

    payout_retry_count = Column(Integer, default=0, nullable=False)
    max_payout_retries = Column(Integer, default=3, nullable=False)

    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)
    funding_completed_at = Column(DateTime, nullable=True)
    payout_initiated_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User")
    funding_steps = relationship("FundingStep", back_populates="funded_transfer", order_by="FundingStep.sequence")

    def __repr__(self):
        return f"<FundedTransfer(id={self.id}, amount={self.amount}, status={self.status})>"


class FundingStep(Base):
    """
    Individual debit from a source account.

    Represents a single direct debit via Mono as part of a FundedTransfer.
    """

    __tablename__ = "funding_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    funded_transfer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("funded_transfers.id", name="fk_funding_steps_funded_transfer_id"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_funding_steps_account_id"),
        nullable=False,
        index=True,
    )
    amount = Column(Float, nullable=False)
    sequence = Column(Integer, nullable=False)
    status = Column(String, default=FundingStepStatusEnum.PENDING.value, nullable=False, index=True)

    provider_name = Column(String, nullable=True)
    provider_debit_id = Column(String, nullable=True, index=True)
    provider_reference = Column(String, unique=True, nullable=True, index=True)

    initiated_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)

    error_message = Column(String, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)

    funded_transfer = relationship("FundedTransfer", back_populates="funding_steps")
    account = relationship("Account")

    def __repr__(self):
        return f"<FundingStep(id={self.id}, amount={self.amount}, status={self.status}, seq={self.sequence})>"


class ProcessedWebhookEvent(Base):
    """Provider webhook event ledger for duplicate delivery protection."""

    __tablename__ = "processed_webhook_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    provider = Column(String, nullable=False, index=True)
    event_id = Column(String, nullable=False)
    event_name = Column(String, nullable=False, index=True)
    status = Column(String, default="processing", nullable=False, index=True)
    payload_hash = Column(String, nullable=True)
    attempt_count = Column(Integer, default=1, nullable=False)

    first_seen_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    last_seen_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_processed_webhook_events_provider_event_id"),
    )

    def __repr__(self):
        return f"<ProcessedWebhookEvent(provider={self.provider}, event={self.event_name}, status={self.status})>"


class ActionableMessage(Base):
    """Actionable message database model for quote-based transaction repeats."""

    __tablename__ = "actionable_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_actionable_messages_user_id"),
        nullable=False,
        index=True,
    )
    channel_message_id = Column(String, unique=True, nullable=False, index=True)
    message_type = Column(String, nullable=False, index=True)  # Uses ActionableMessageTypeEnum
    message_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)

    user = relationship("User")

    def __repr__(self):
        return f"<ActionableMessage(id={self.id}, channel_msg_id={self.channel_message_id}, type={self.message_type})>"


class ScheduledInstruction(Base):
    """Scheduled instruction for recurring and one-time future transactions."""

    __tablename__ = "scheduled_instructions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_scheduled_instructions_user_id"),
        nullable=False,
        index=True,
    )
    domain = Column(String, default=ScheduleDomainEnum.TRANSFER.value, nullable=False, index=True)
    status = Column(String, default=ScheduledInstructionStatusEnum.ACTIVE.value, nullable=False, index=True)
    action = Column(String, nullable=False)
    payload_snapshot = Column(JSON, nullable=False, default={})
    timezone = Column(String, nullable=False, default="Africa/Lagos")
    recurrence_type = Column(String, default=RecurrenceTypeEnum.ONE_TIME.value, nullable=False, index=True)
    start_date = Column(String, nullable=False)
    local_time = Column(String, nullable=False)
    day_of_week = Column(Integer, nullable=True)
    day_of_month = Column(Integer, nullable=True)
    end_date = Column(String, nullable=True)
    next_run_at_utc = Column(DateTime, nullable=False, index=True)
    last_run_at_utc = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    channel = Column(String, nullable=False, default="whatsapp")
    channel_identity = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)

    user = relationship("User", back_populates="scheduled_instructions")
    runs = relationship("ScheduledRun", back_populates="schedule", order_by="ScheduledRun.due_at_utc")

    def __repr__(self):
        return (
            f"<ScheduledInstruction(id={self.id}, domain={self.domain}, status={self.status}, "
            f"recurrence={self.recurrence_type})>"
        )


class ScheduledRun(Base):
    """Single execution attempt for a scheduled instruction."""

    __tablename__ = "scheduled_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    schedule_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_instructions.id", name="fk_scheduled_runs_schedule_id"),
        nullable=False,
        index=True,
    )
    due_at_utc = Column(DateTime, nullable=False, index=True)
    status = Column(String, default=ScheduledRunStatusEnum.QUEUED.value, nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    transaction_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    schedule = relationship("ScheduledInstruction", back_populates="runs")

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_code = Column(String(20), unique=True, nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_support_tickets_user_id"),
        nullable=False,
        index=True,
    )
    channel = Column(String(20), default="whatsapp", nullable=False)
    intent = Column(String(50), nullable=False, index=True)
    status = Column(String(20), default=SupportTicketStatusEnum.OPEN.value, nullable=False, index=True)
    priority = Column(String(10), default=SupportTicketPriorityEnum.MEDIUM.value, nullable=False, index=True)

    transaction_ref = Column(String(100), nullable=True, index=True)

    summary = Column(Text, nullable=False)
    details = Column(JSON, default={}, nullable=False)

    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=utc_now_naive, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship("User")

    def __repr__(self):
        return f"<SupportTicket(id={self.id}, code={self.ticket_code}, status={self.status})>"
