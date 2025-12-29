"""Response context for response generation."""

from dataclasses import dataclass, field
from typing import Any

from .intent import ResponseIntent


@dataclass
class ResponseContext:
    """Context data for response generation.

    Contains all information needed to generate a natural response.
    """

    intent: ResponseIntent

    user_name: str | None = None
    language: str = "en"

    amount: float | None = None
    formatted_amount: str | None = None

    recipient_name: str | None = None
    recipient_account: str | None = None
    recipient_account_masked: str | None = None
    bank_name: str | None = None
    bank_code: str | None = None

    phone_number: str | None = None
    phone_masked: str | None = None
    network: str | None = None
    data_plan: str | None = None

    source_account_name: str | None = None
    source_bank_name: str | None = None
    balance: float | None = None

    candidates: list[dict[str, Any]] = field(default_factory=list)

    error_message: str | None = None
    error_code: str | None = None

    transaction_id: str | None = None
    transaction_reference: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    def format_amount(self) -> str:
        """Format amount as currency string."""
        if self.formatted_amount:
            return self.formatted_amount
        if self.amount is not None:
            if self.amount == int(self.amount):
                return f"₦{int(self.amount):,}"
            return f"₦{self.amount:,.2f}"
        return ""

    def mask_account(self, account: str | None) -> str:
        """Mask account number showing last 4 digits."""
        if not account or len(account) < 4:
            return account or ""
        return f"…{account[-4:]}"

    def mask_phone(self, phone: str | None) -> str:
        """Mask phone number showing last 4 digits."""
        if not phone or len(phone) < 4:
            return phone or ""
        return f"…{phone[-4:]}"
