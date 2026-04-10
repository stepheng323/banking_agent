"""Mono implementation of DirectDebitProvider.

Uses Mono's Direct Debit API for pulling funds from user bank accounts.
"""

from shared.clients.abstractions.direct_debit import (
    BalanceResult,
    DebitResult,
    DebitStatus,
    DirectDebitProvider,
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

    def __init__(self, mono_client: MonoClient | None = None):
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
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> DebitResult:
        """Initiate a one-time debit via Mono Direct Debit API."""
        has_beneficiary_account = bool(beneficiary_account)
        has_beneficiary_bank = bool(beneficiary_bank_code)
        if has_beneficiary_account != has_beneficiary_bank:
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=reference,
                amount=amount,
                error_message="Both beneficiary_account and beneficiary_bank_code must be provided together",
            )

        try:
            amount_kobo = int(amount * 100)
            mode = "direct_beneficiary" if has_beneficiary_account else "pooling"

            response = await self._client.initiate_debit(
                mandate_id=mandate_id,
                amount=amount_kobo,
                reference=reference,
                narration=narration,
                beneficiary_account=beneficiary_account,
                beneficiary_bank_code=beneficiary_bank_code,
            )
            logger.info("mono_initiate_debit_mode", mode=mode, reference=reference)
            success, status, error_message = self._normalize_debit_outcome(response)

            return DebitResult(
                success=success,
                status=status,
                debit_id=response.get("id"),
                reference=response.get("reference") or reference,
                amount=amount,
                error_message=error_message,
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
            success, status, error_message = self._normalize_debit_outcome(response)

            return DebitResult(
                success=success,
                status=status,
                debit_id=debit_id,
                reference=response.get("reference"),
                amount=response.get("amount", 0) / 100,  # Kobo to Naira
                error_message=error_message,
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
        del reason
        logger.warning("mono_reverse_debit_not_implemented", debit_id=debit_id)
        return DebitResult(
            success=False,
            status=DebitStatus.FAILED,
            debit_id=debit_id,
            error_message="Direct debit reversal not yet implemented for Mono",
        )

    async def cancel_mandate(self, mandate_id: str) -> bool:
        """Cancel a mandate via Mono."""
        try:
            return await self._client.cancel_mandate(mandate_id)
        except Exception as e:
            logger.error("mono_cancel_mandate_failed", mandate_id=mandate_id, error=str(e))
            return False

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

    @staticmethod
    def _response_code(response: dict | None) -> str | None:
        """Extract Mono response code when present."""
        if not isinstance(response, dict):
            return None
        code = response.get("response_code")
        if code is None:
            code = response.get("responseCode")
        return None if code is None else str(code)

    @staticmethod
    def _response_message(response: dict | None) -> str | None:
        """Extract the best available provider error message."""
        if not isinstance(response, dict):
            return None
        for key in ("message", "response_message", "description", "reason"):
            value = response.get(key)
            if value is not None:
                return str(value)
        return None

    def _normalize_debit_outcome(self, response: dict | None) -> tuple[bool, DebitStatus, str | None]:
        """Normalize Mono debit status using response_code when available."""
        mono_status = str((response or {}).get("status", "pending"))
        mapped_status = self._map_status(mono_status)
        response_code = self._response_code(response)

        if response_code is None:
            success = mapped_status != DebitStatus.FAILED
            error_message = self._response_message(response) if mapped_status == DebitStatus.FAILED else None
            return success, mapped_status, error_message

        if mapped_status == DebitStatus.SUCCESSFUL:
            if response_code == "00":
                return True, DebitStatus.SUCCESSFUL, None
            return False, DebitStatus.FAILED, self._response_message(response)

        if mapped_status == DebitStatus.FAILED:
            return False, DebitStatus.FAILED, self._response_message(response)

        return True, mapped_status, None
