"""Mock implementation of DirectDebitProvider for development and testing."""
import uuid
from typing import Dict

from shared.clients.abstractions.direct_debit import (
    DirectDebitProvider,
    DebitResult,
    DebitStatus,
    BalanceResult,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MockDirectDebitProvider(DirectDebitProvider):
    """
    Mock implementation of DirectDebitProvider for development.
    
    Simulates debit operations with configurable balances and behaviors.
    """
    
    def __init__(self):
        # Simulated account balances (account_id -> balance in naira)
        self._balances: Dict[str, float] = {
            "mock_account_1": 150000.0,
            "mock_account_2": 75000.0,
            "mock_account_3": 25000.0,
        }
        # Track debits (reference -> debit info)
        self._debits: Dict[str, dict] = {}
    
    @property
    def provider_name(self) -> str:
        return "mock"
    
    def set_balance(self, account_id: str, balance: float) -> None:
        """Set balance for testing."""
        self._balances[account_id] = balance
    
    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """Get simulated balance."""
        balance = self._balances.get(account_id, 50000.0)  # Default 50k
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
        narration: str = "Transfer funding"
    ) -> DebitResult:
        """Simulate debit initiation."""
        debit_id = f"mock_debit_{uuid.uuid4().hex[:8]}"
        
        # Store debit info
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
        # Find the debit by ID
        for ref, debit in self._debits.items():
            if debit["debit_id"] == debit_id:
                # Simulate progression to successful
                debit["status"] = DebitStatus.SUCCESSFUL
                return DebitResult(
                    success=True,
                    status=DebitStatus.SUCCESSFUL,
                    debit_id=debit_id,
                    reference=debit["reference"],
                    amount=debit["amount"],
                )
        
        # Not found - return as successful anyway for testing
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
    
    def simulate_debit_success(self, reference: str) -> None:
        """Mark a debit as successful (for testing webhooks)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.SUCCESSFUL
    
    def simulate_debit_failure(self, reference: str) -> None:
        """Mark a debit as failed (for testing failure paths)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.FAILED
