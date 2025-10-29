"""Pydantic models for user-related data."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """Model for creating a new user."""

    phone_number: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    onboarding_status: Optional[str] = Field(default=None)
    transaction_pin: Optional[str] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    """Model for updating user data."""

    full_name: Optional[str] = None
    email: Optional[str] = None
    transaction_pin: Optional[str] = None
    onboarding_status: Optional[str] = Field(default=None)
    extra_data: Optional[Dict[str, Any]] = None
