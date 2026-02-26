"""Abstract base class for direct debit providers.

This abstraction allows swapping between providers (Mono, Paystack, etc.)
without changing the core funding logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class DebitStatus(str, Enum):
    """Status of a direct debit transaction."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    REVERSED = "reversed"


@dataclass
class DebitResult:
    """Result of initiating or checking a debit."""

    success: bool
    status: DebitStatus
    debit_id: str | None = None
    reference: str | None = None
    amount: float | None = None  # In naira
    error_message: str | None = None
    provider_response: dict | None = None


@dataclass
class BalanceResult:
    """Result of checking account balance."""

    success: bool
    available_balance: float  # In naira
    ledger_balance: float | None = None
    currency: str = "NGN"
    error_message: str | None = None


@dataclass
class AccountInfo:
    """Account information for direct debit eligibility."""

    account_id: str  # Internal account ID
    mandate_id: str | None = None  # Provider's mandate ID
    mandate_status: str = "pending"  # pending, ready, expired, cancelled
    account_number: str = ""
    bank_code: str = ""
    bank_name: str = ""


class DirectDebitProvider(ABC):
    """
    Abstract base class for direct debit providers.

    Implementations:
    - MonoDirectDebitProvider (production)
    - MockDirectDebitProvider (development/testing)
    - PaystackDirectDebitProvider (future)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'mono', 'paystack')."""
        pass

    @abstractmethod
    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """
        Get the current balance of an account.

        Args:
            account_id: Provider's account identifier
            real_time: Whether to fetch real-time balance (may be slower)

        Returns:
            BalanceResult with available balance
        """
        pass

    @abstractmethod
    async def initiate_debit(
        self,
        mandate_id: str,
        amount: float,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> DebitResult:
        """
        Initiate a one-time debit against a mandate.

        Args:
            mandate_id: Provider's mandate identifier
            amount: Amount to debit in naira
            reference: Unique reference for this debit
            narration: Description for the transaction
            beneficiary_account: If provided, direct-to-beneficiary transfer
            beneficiary_bank_code: Required if beneficiary_account provided

        Returns:
            DebitResult with debit_id and initial status
        """
        pass

    async def initiate_pooling_debit(
        self,
        mandate_id: str,
        amount: float,
        reference: str,
        narration: str = "Transfer",
    ) -> DebitResult:
        """Initiate debit-only pull for pooled funding (no beneficiary transfer)."""
        return await self.initiate_debit(
            mandate_id=mandate_id,
            amount=amount,
            reference=reference,
            narration=narration,
            beneficiary_account=None,
            beneficiary_bank_code=None,
        )

    async def initiate_direct_beneficiary_debit(
        self,
        mandate_id: str,
        amount: float,
        reference: str,
        beneficiary_account: str,
        beneficiary_bank_code: str,
        narration: str = "Transfer",
    ) -> DebitResult:
        """Initiate direct-to-beneficiary debit transfer."""
        if not beneficiary_account or not beneficiary_bank_code:
            raise ValueError("beneficiary_account and beneficiary_bank_code are required")
        return await self.initiate_debit(
            mandate_id=mandate_id,
            amount=amount,
            reference=reference,
            narration=narration,
            beneficiary_account=beneficiary_account,
            beneficiary_bank_code=beneficiary_bank_code,
        )

    @abstractmethod
    async def get_debit_status(self, debit_id: str) -> DebitResult:
        """
        Get the current status of a debit transaction.

        Args:
            debit_id: Provider's debit transaction ID

        Returns:
            DebitResult with current status
        """
        pass

    @abstractmethod
    async def reverse_debit(self, debit_id: str, reason: str = "Refund") -> DebitResult:
        """
        Reverse/refund a completed debit.

        Args:
            debit_id: Provider's debit transaction ID
            reason: Reason for the reversal

        Returns:
            DebitResult with reversal status
        """
        pass

    @abstractmethod
    async def cancel_mandate(self, mandate_id: str) -> bool:
        """
        Cancel/revoke a mandate.

        Args:
            mandate_id: Provider's mandate identifier

        Returns:
            True if cancellation was successful
        """
        pass

    def is_mandate_ready(self, account: AccountInfo) -> bool:
        """Check if an account's mandate is ready for debiting."""
        return account.mandate_status == "ready" and account.mandate_id is not None
