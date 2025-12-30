"""SQLAlchemy database models."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import text

from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum

Base = declarative_base()


class UserOnboardingStatusEnum(str, Enum):
    """User onboarding status enum."""

    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_COMPLETED = "onboarding_completed"


class User(Base):
    """User database model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    address = Column(String, nullable=True)
    mono_customer_id = Column(String, nullable=True, index=True)

    onboarding_status = Column(String, nullable=True)
    last_active = Column(DateTime, default=datetime.utcnow)
    extra_data = Column(JSON, default={})
    transaction_pin = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False)

    accounts = relationship("Account", back_populates="user")
    beneficiaries = relationship("Beneficiary", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, phone={self.phone_number}, name={self.full_name})>"


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
    mandate_status = Column(String, default="pending", nullable=False)
    extra_data = Column(JSON, default={})
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False)

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
    beneficiary_type = Column(String, default="transfer", nullable=False, index=True)
    account_name = Column(String, nullable=False)
    alias = Column(String, nullable=True)
    account_number = Column(String, nullable=True)
    bank_code = Column(String, nullable=True)
    bank_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="beneficiaries")

    def __repr__(self):
        return (
            f"<Beneficiary(id={self.id}, type={self.beneficiary_type}, "
            f"name={self.account_name}, account={self.account_number})>"
        )


class Transaction(Base):
    """Transaction database model for logging all transfers."""

    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_transactions_user_id"),
        nullable=False,
        index=True,
    )
    transaction_type = Column(String, default="transfer", nullable=False)
    status = Column(String, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="NGN", nullable=False)
    source_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", name="fk_transactions_source_account_id"),
        nullable=True,
    )
    source_account_number = Column(String, nullable=False)
    source_bank_name = Column(String, nullable=False)
    recipient_account_number = Column(String, nullable=False)
    recipient_bank_code = Column(String, nullable=False)
    recipient_bank_name = Column(String, nullable=False)
    recipient_name = Column(String, nullable=False)
    narration = Column(String, nullable=True)
    transaction_id = Column(String, nullable=True)
    idempotency_key = Column(String, unique=True, nullable=False, index=True)
    error_message = Column(String, nullable=True)
    provider_response = Column(JSON, nullable=True)
    provider_status = Column(String, nullable=True)  # Provider's transaction status
    provider_error_code = Column(String, nullable=True)  # Provider-specific error code
    receipt_sent = Column(Boolean, default=False, nullable=False)
    beneficiary_suggested = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, status={self.status}, amount={self.amount}, tx_id={self.transaction_id})>"


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
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False)
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
    wa_message_id = Column(String, unique=True, nullable=False, index=True)
    message_type = Column(String, nullable=False, index=True)
    message_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)

    user = relationship("User")

    def __repr__(self):
        return f"<ActionableMessage(id={self.id}, wa_msg_id={self.wa_message_id}, type={self.message_type})>"
