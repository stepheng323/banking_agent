"""Mono implementation of DirectDebitProvider.

Uses Mono's Direct Debit API for pulling funds from user bank accounts.
"""
from typing import Optional

from shared.clients.abstractions.direct_debit import (
    DirectDebitProvider,
    DebitResult,
    DebitStatus,
    BalanceResult,
)
from shared.clients.providers.mono.client import MonoClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MonoDirectDebitProvider(DirectDebitProvider):
    """
    Mono implementation of DirectDebitProvider.
    
    Uses Mono's v3 Direct Debit API for:
    - Balance checks (via account_id)
    - One-time debits (via mandate_id)
    - Debit status checks
    - Reversals/refunds
    """
    
    def __init__(self, mono_client: Optional[MonoClient] = None):
        self._client = mono_client or MonoClient()
    
    @property
    def provider_name(self) -> str:
        return "mono"
    
    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """Get account balance via Mono."""
        try:
            balance_data = await self._client.get_balance(account_id, real_time=real_time)
            return BalanceResult(
                success=True,
                available_balance=balance_data.balance_naira,
                ledger_balance=balance_data.ledger_balance_naira,
                currency=balance_data.currency,
            )
        except Exception as e:
            logger.error("mono_get_balance_failed", account_id=account_id, error=str(e))
            return BalanceResult(
                success=False,
                available_balance=0,
                error_message=str(e),
            )
    
    async def initiate_debit(
        self,
        mandate_id: str,
        amount: float,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: Optional[str] = None,
        beneficiary_bank_code: Optional[str] = None,
    ) -> DebitResult:
        """Initiate a one-time debit via Mono Direct Debit API."""
        try:
            amount_kobo = int(amount * 100)
            
            response = await self._client.initiate_debit(
                mandate_id=mandate_id,
                amount=amount_kobo,
                reference=reference,
                narration=narration,
                beneficiary_account=beneficiary_account,
                beneficiary_bank_code=beneficiary_bank_code,
            )
            
            return DebitResult(
                success=True,
                status=self._map_status(response.get("status", "pending")),
                debit_id=response.get("id"),
                reference=reference,
                amount=amount,
                provider_response=response,
            )
        except Exception as e:
            logger.error("mono_initiate_debit_failed", mandate_id=mandate_id, error=str(e))
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=reference,
                amount=amount,
                error_message=str(e),
            )
    
    async def get_debit_status(self, debit_id: str) -> DebitResult:
        """Get debit status from Mono."""
        try:
            response = await self._client.get_debit_status(debit_id)
            
            return DebitResult(
                success=True,
                status=self._map_status(response.get("status", "pending")),
                debit_id=debit_id,
                reference=response.get("reference"),
                amount=response.get("amount", 0) / 100,  # Kobo to Naira
                provider_response=response,
            )
        except Exception as e:
            logger.error("mono_get_debit_status_failed", debit_id=debit_id, error=str(e))
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                debit_id=debit_id,
                error_message=str(e),
            )
    
    async def reverse_debit(self, debit_id: str, reason: str = "Refund") -> DebitResult:
        """Reverse a debit via Mono (if supported)."""
        # Note: Mono may not support direct reversals - this would trigger a refund flow
        logger.warning("mono_reverse_debit_not_implemented", debit_id=debit_id)
        return DebitResult(
            success=False,
            status=DebitStatus.FAILED,
            debit_id=debit_id,
            error_message="Direct debit reversal not yet implemented for Mono",
        )
    
    def _map_status(self, mono_status: str) -> DebitStatus:
        """Map Mono status to our DebitStatus enum."""
        status_map = {
            "pending": DebitStatus.PENDING,
            "processing": DebitStatus.PROCESSING,
            "successful": DebitStatus.SUCCESSFUL,
            "success": DebitStatus.SUCCESSFUL,
            "failed": DebitStatus.FAILED,
            "reversed": DebitStatus.REVERSED,
        }
        return status_map.get(mono_status.lower(), DebitStatus.PENDING)
