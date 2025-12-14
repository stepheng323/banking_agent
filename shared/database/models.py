"""SQLAlchemy database models."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import text

Base = declarative_base()


class UserOnboardingStatusEnum(str, Enum):
    """User onboarding status enum."""

    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_COMPLETED = "onboarding_completed"


class User(Base):
    """User database model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True,
                default=uuid.uuid4, index=True)
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
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    accounts = relationship("Account", back_populates="user")
    beneficiaries = relationship("Beneficiary", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, phone={self.phone_number}, name={self.full_name})>"


class Account(Base):
    """Bank account database model."""

    __tablename__ = "accounts"

    id = Column(UUID(as_uuid=True), primary_key=True,
                default=uuid.uuid4, index=True)
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
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User", back_populates="accounts")

    def __repr__(self):
        return f"<Account(id={self.id}, bank={self.bank_name}, number={self.account_number})>"


class Beneficiary(Base):
    """Beneficiary database model."""

    __tablename__ = "beneficiaries"

    id = Column(UUID(as_uuid=True), primary_key=True,
                default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True),
                     ForeignKey("users.id", name="fk_beneficiaries_user_id"),
                     nullable=False, index=True)
    beneficiary_type = Column(
        String, default="transfer", nullable=False, index=True)
    account_name = Column(String, nullable=False)
    alias = Column(String, nullable=True)
    account_number = Column(String, nullable=True)
    bank_code = Column(String, nullable=True)
    bank_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User", back_populates="beneficiaries")

    def __repr__(self):
        return f"<Beneficiary(id={self.id}, type={self.beneficiary_type}, name={self.account_name}, account_number={self.account_number}, bank_code={self.bank_code}, bank_name={self.bank_name})>"


class Transaction(Base):
    """Transaction database model for logging all transfers."""

    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True,
                default=uuid.uuid4, index=True)
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
    receipt_sent = Column(Boolean, default=False, nullable=False)
    beneficiary_suggested = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=text(
        "now()"), nullable=False, index=True)
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(id={self.id}, status={self.status}, amount={self.amount}, transaction_id={self.transaction_id})>"
