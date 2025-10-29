"""SQLAlchemy database models."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
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

    onboarding_status = Column(String, nullable=True)
    last_active = Column(DateTime, default=datetime.utcnow)
    extra_data = Column(JSON, default={})
    transaction_pin = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

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
    account_number = Column(String, nullable=False)
    account_name = Column(String, nullable=True)
    is_default = Column(Boolean, default=False)
    extra_data = Column(JSON, default={})
    created_at = Column(DateTime, server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime, server_default=text("now()"), onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<Account(id={self.id}, bank={self.bank_name}, number={self.account_number})>"
