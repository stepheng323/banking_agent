"""Account models for data validation and serialization."""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CreateAccount(BaseModel):
    """Model for creating a bank account."""

    user_id: str
    account_id: str
    bank_name: str
    account_number: str
    account_name: Optional[str] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)
