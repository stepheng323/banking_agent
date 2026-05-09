"""Payout executor for funded multi-source transfers."""

from typing import Any

from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.abstractions.resolution import AccountResolverProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PayoutExecutor:
    """Executes a single beneficiary payout after funding steps succeed."""

    def __init__(self, payout_provider: PayoutProvider, resolver_provider: AccountResolverProvider):
        self.payout_provider = payout_provider
        self.resolver_provider = resolver_provider

    async def handle_payout(self, data: dict[str, Any]) -> dict[str, Any]:
        """Execute payout via configured payment provider."""
        amount = float(data.get("amount") or 0.0)
        recipient_account = str(data.get("recipient_account") or "")
        recipient_bank_code = str(data.get("recipient_bank_code") or "")
        narration = data.get("narration")

        if amount <= 0:
            return {
                "success": False,
                "status": "failed",
                "error": "Invalid payout amount",
                "provider": self.payout_provider.provider_name,
            }
        if not recipient_account or not recipient_bank_code:
            return {
                "success": False,
                "status": "failed",
                "error": "Missing recipient account details",
                "provider": self.payout_provider.provider_name,
            }

        logger.info(
            "payout_executor_start",
            provider=self.payout_provider.provider_name,
            amount=amount,
            recipient_bank_code=recipient_bank_code,
        )

        resolution = await self.resolver_provider.resolve_account(recipient_account, recipient_bank_code)
        if not resolution.success or resolution.account is None:
            error = str(resolution.error or "Recipient account verification failed")
            logger.error(
                "payout_executor_recipient_resolution_failed",
                provider=self.payout_provider.provider_name,
                recipient_account=recipient_account,
                recipient_bank_code=recipient_bank_code,
                error=error,
            )
            return {
                "success": False,
                "status": "failed",
                "error": error,
                "provider": self.payout_provider.provider_name,
            }

        verified_account = str(resolution.account.account_number or recipient_account)
        verified_bank_code = str(resolution.account.bank_code or recipient_bank_code)

        result = await self.payout_provider.initiate_transfer(
            amount=amount,
            recipient_account_number=verified_account,
            recipient_bank_code=verified_bank_code,
            narration=narration,
        )

        success = bool(result.get("success") or result.get("status") == "success")
        return {
            **result,
            "success": success,
            "resolved_account_name": resolution.account.account_name,
        }
