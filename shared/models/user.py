"""Pydantic models for user-related data."""

from typing import Any

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """Model for creating a new user."""

    phone_number: str
    full_name: str | None = None
    email: str | None = None
    onboarding_status: str | None = Field(default=None)
    transaction_pin: str | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    """Model for updating user data."""

    full_name: str | None = None
    email: str | None = None
    address: str | None = None
    mono_customer_id: str | None = None
    transaction_pin: str | None = None
    onboarding_status: str | None = Field(default=None)
    extra_data: dict[str, Any] | None = None
