"""Payout executor for funded multi-source transfers."""

from typing import Any

from shared.clients.abstractions.payment import PaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PayoutExecutor:
    """Executes a single beneficiary payout after funding steps succeed."""

    def __init__(self, payment_provider: PaymentProvider):
        self.payment_provider = payment_provider

    async def handle_payout(self, data: dict[str, Any]) -> dict[str, Any]:
        """Execute payout via configured payment provider."""
        amount = float(data.get("amount") or 0.0)
        recipient_account = str(data.get("recipient_account") or "")
        recipient_bank_code = str(data.get("recipient_bank_code") or "")
        narration = data.get("narration")

        logger.info(
            "payout_executor_start",
            provider=self.payment_provider.provider_name,
            amount=amount,
            recipient_bank_code=recipient_bank_code,
        )

        result = await self.payment_provider.initiate_transfer(
            amount=amount,
            recipient_account_number=recipient_account,
            recipient_bank_code=recipient_bank_code,
            narration=narration,
        )

        success = bool(result.get("success") or result.get("status") == "success")
        return {
            **result,
            "success": success,
        }
