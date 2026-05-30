"""Abstract base class for bank data providers.

This abstraction allows swapping between providers (Mono, Okra, etc.)
for account data, transactions, and BVN verification.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from shared.money import MoneyAmount


@dataclass
class AccountData:
    """Bank account details."""

    account_id: str
    account_name: str
    account_number: str
    account_type: str
    bank_name: str
    bank_code: str | None = None


@dataclass
class BalanceData:
    """Account balance information."""

    available_balance: MoneyAmount
    ledger_balance: MoneyAmount | None = None
    currency: str = "NGN"
    account_id: str | None = None


@dataclass
class TransactionData:
    """A single transaction."""

    transaction_id: str | None
    date: str
    narration: str
    amount: MoneyAmount
    transaction_type: str
    category: str | None = None
    counterparty: str | None = None


@dataclass
class TransactionPageData:
    """A page of transactions from the provider."""

    transactions: list[TransactionData]
    page: int
    has_more: bool
    next_page: int | None = None


@dataclass
class BvnLookupResult:
    """Result of BVN lookup initiation."""

    success: bool
    session_id: str | None = None
    bvn: str | None = None
    verification_methods: list[dict] | None = None
    error_message: str | None = None


@dataclass
class BvnVerificationResult:
    """Result of BVN verification."""

    success: bool
    accounts: list[dict] | None = None
    customer_data: dict | None = None
    error_message: str | None = None


class BankDataProvider(ABC):
    """
    Abstract interface for bank data providers.

    Handles account data, transactions, and BVN verification.
    Implementations: MonoClient, OkraClient (future), etc.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'mono', 'okra')."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is properly configured."""
        pass

    @abstractmethod
    async def get_account(self, account_id: str) -> AccountData | None:
        """
        Get account details.

        Args:
            account_id: Provider's account identifier

        Returns:
            AccountData or None if not found
        """
        pass

    @abstractmethod
    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceData | None:
        """
        Get account balance.

        Args:
            account_id: Provider's account identifier
            real_time: Whether to fetch real-time (may be slower)

        Returns:
            BalanceData or None if failed
        """
        pass

    @abstractmethod
    async def get_transactions(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        transaction_type: str | None = None,
        limit: int = 50,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[TransactionData]:
        """
        Get account transactions.

        Args:
            account_id: Provider's account identifier
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            transaction_type: Filter by "credit" or "debit"
            limit: Max transactions to return
            user_id: Optional user context for mock/dev providers
            mock_account_slot: Optional linked-account slot for mock/dev providers

        Returns:
            List of TransactionData
        """
        pass

    async def get_transactions_page(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        page: int = 1,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> TransactionPageData:
        """Get one page of account transactions.

        Providers without first-class pagination support may override this
        method. The default fallback wraps `get_transactions()` as a single page.
        """
        transactions = await self.get_transactions(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            user_id=user_id,
            mock_account_slot=mock_account_slot,
        )
        has_more = len(transactions) >= limit
        return TransactionPageData(
            transactions=transactions,
            page=page,
            has_more=has_more,
            next_page=page + 1 if has_more else None,
        )

    @abstractmethod
    async def initiate_bvn_lookup(self, bvn: str) -> BvnLookupResult:
        """
        Initiate BVN verification to get available methods.

        Args:
            bvn: 11-digit BVN

        Returns:
            BvnLookupResult with session_id and methods
        """
        pass

    @abstractmethod
    async def verify_bvn(self, session_id: str, method: str) -> dict[str, Any]:
        """
        Send verification code via selected method.

        Args:
            session_id: Session from initiate_bvn_lookup
            method: Verification method (e.g., "phone", "email")

        Returns:
            Dict with status
        """
        pass

    @abstractmethod
    async def verify_otp(self, session_id: str, otp: str) -> BvnVerificationResult:
        """
        Verify OTP and get linked bank accounts.

        Args:
            session_id: Session from initiate_bvn_lookup
            otp: One-time password sent to user

        Returns:
            BvnVerificationResult with accounts and customer data
        """
        pass
