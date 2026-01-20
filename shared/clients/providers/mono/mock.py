"""Mock implementation of DirectDebitProvider for development and testing."""

import uuid

from shared.clients.abstractions.direct_debit import (
    BalanceResult,
    DebitResult,
    DebitStatus,
    DirectDebitProvider,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MOCK_BALANCE = 30000.0


class MockDirectDebitProvider(DirectDebitProvider):
    """
    Mock implementation of DirectDebitProvider for development.

    Simulates debit operations with configurable balances and behaviors.
    Default balance is 30k to easily test multi-account funding.
    """

    def __init__(self):
        self._balances: dict[str, float] = {
            "mock_account_1": 150000.0,
            "mock_account_2": 75000.0,
            "mock_account_3": 25000.0,
        }
        self._debits: dict[str, dict] = {}

    @property
    def provider_name(self) -> str:
        return "mock"

    def set_balance(self, account_id: str, balance: float) -> None:
        """Set balance for testing."""
        self._balances[account_id] = balance
        logger.info("mock_balance_set", account_id=account_id, balance=balance)

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """Get simulated balance. Returns 30k default for unknown accounts."""
        balance = self._balances.get(account_id, DEFAULT_MOCK_BALANCE)
        logger.info("mock_get_balance", account_id=account_id, balance=balance)
        return BalanceResult(
            success=True,
            available_balance=balance,
            ledger_balance=balance,
            currency="NGN",
        )

    async def initiate_debit(
        self,
        mandate_id: str,
        amount: float,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> DebitResult:
        """Simulate debit initiation."""
        debit_id = f"mock_debit_{uuid.uuid4().hex[:8]}"

        self._debits[reference] = {
            "debit_id": debit_id,
            "mandate_id": mandate_id,
            "amount": amount,
            "reference": reference,
            "status": DebitStatus.PROCESSING,
        }

        logger.info("mock_initiate_debit", debit_id=debit_id, amount=amount, reference=reference)

        return DebitResult(
            success=True,
            status=DebitStatus.PROCESSING,
            debit_id=debit_id,
            reference=reference,
            amount=amount,
        )

    async def get_debit_status(self, debit_id: str) -> DebitResult:
        """Get simulated debit status (always returns successful for testing)."""
        for _ref, debit in self._debits.items():
            if debit["debit_id"] == debit_id:
                debit["status"] = DebitStatus.SUCCESSFUL
                return DebitResult(
                    success=True,
                    status=DebitStatus.SUCCESSFUL,
                    debit_id=debit_id,
                    reference=debit["reference"],
                    amount=debit["amount"],
                )

        return DebitResult(
            success=True,
            status=DebitStatus.SUCCESSFUL,
            debit_id=debit_id,
        )

    async def reverse_debit(self, debit_id: str, reason: str = "Refund") -> DebitResult:
        """Simulate debit reversal."""
        logger.info("mock_reverse_debit", debit_id=debit_id, reason=reason)
        return DebitResult(
            success=True,
            status=DebitStatus.REVERSED,
            debit_id=debit_id,
        )

    async def cancel_mandate(self, mandate_id: str) -> bool:
        """Simulate mandate cancellation."""
        logger.info("mock_cancel_mandate", mandate_id=mandate_id)
        return True

    def simulate_debit_success(self, reference: str) -> None:
        """Mark a debit as successful (for testing webhooks)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.SUCCESSFUL

    def simulate_debit_failure(self, reference: str) -> None:
        """Mark a debit as failed (for testing failure paths)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.FAILED
