"""Mono implementation of BankingDataProvider."""

from typing import Any

from shared.clients.abstractions.banking import (
    AccountData,
    BalanceData,
    BankingDataProvider,
    BvnLookupResult,
    BvnVerificationResult,
    ResolvedAccount,
    TransactionData,
)
from shared.clients.providers.mono.client import MonoClient
from shared.clients.providers.mono.models import MonoApiError
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MonoBankingProvider(BankingDataProvider):
    """Mono implementation of BankingDataProvider."""

    def __init__(self, client: MonoClient | None = None):
        self._client = client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    @property
    def is_available(self) -> bool:
        return bool(settings.mono_api_key)

    async def get_account(self, account_id: str) -> AccountData | None:
        try:
            acc = await self._client.get_account(account_id)
            return AccountData(
                account_id=acc.id,
                account_name=acc.name,
                account_number=acc.account_number,
                account_type=acc.type,
                bank_name=acc.institution.name,
                bank_code=acc.institution.bank_code,
            )
        except MonoApiError as e:
            logger.error("get_account_failed", account_id=account_id, error=str(e))
            return None

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceData | None:
        try:
            bal = await self._client.get_balance(account_id, real_time=real_time)
            return BalanceData(
                available_balance=bal.balance_naira,
                ledger_balance=bal.ledger_balance_naira,
                currency=bal.currency,
                account_id=account_id,
            )
        except MonoApiError as e:
            logger.error("get_balance_failed", account_id=account_id, error=str(e))
            return None

    async def get_transactions(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        transaction_type: str | None = None,
        limit: int = 50,
    ) -> list[TransactionData]:
        try:
            txns = await self._client.get_transactions(
                account_id=account_id,
                start=start_date,
                end=end_date,
                transaction_type=transaction_type,
                limit=limit,
            )
            return [
                TransactionData(
                    transaction_id=t.id,
                    date=t.date,
                    narration=t.narration,
                    amount=t.amount / 100 if t.type == "credit" else -t.amount / 100,
                    transaction_type=t.type,
                    category=t.category,
                )
                for t in txns
            ]
        except MonoApiError as e:
            logger.error("get_transactions_failed", account_id=account_id, error=str(e))
            return []

    async def initiate_bvn_lookup(self, bvn: str) -> BvnLookupResult:
        try:
            result = await self._client.initiate_bvn_lookup(bvn)
            return BvnLookupResult(
                success=True,
                session_id=result.session_id,
                bvn=result.bvn,
                verification_methods=[{"method": m.method, "hint": m.hint} for m in result.methods],
            )
        except MonoApiError as e:
            logger.error("bvn_lookup_failed", error=str(e))
            return BvnLookupResult(success=False, error_message=str(e))

    async def verify_bvn(self, session_id: str, method: str) -> dict:
        try:
            await self._client.verify_bvn(session_id, method)
            return {"success": True}
        except MonoApiError as e:
            logger.error("bvn_verify_failed", error=str(e))
            return {"success": False, "error": str(e)}

    async def verify_otp(self, session_id: str, otp: str) -> BvnVerificationResult:
        try:
            accounts = await self._client.verify_otp(session_id, otp)
            return BvnVerificationResult(
                success=True,
                accounts=[
                    {
                        "account_name": a.account_name,
                        "account_number": a.account_number,
                        "account_type": a.account_type,
                        "bank_name": a.institution.name,
                        "bank_code": a.institution.bank_code,
                    }
                    for a in accounts
                ],
            )
        except MonoApiError as e:
            logger.error("otp_verify_failed", error=str(e))
            return BvnVerificationResult(success=False, error_message=str(e))

    async def resolve_account_number(self, account_number: str, bank_code: str) -> ResolvedAccount | None:
        try:
            data = await self._client.lookup_account_number(account_number, bank_code)
            if not data:
                return None

            return ResolvedAccount(
                account_name=data.name,
                account_number=data.account_number,
            )
        except Exception as e:
            logger.error("resolve_account_failed", error=str(e))
            return None

    async def get_banks(self) -> dict[str, Any]:
        """Fetch banks from Mono."""
        try:
            banks = await self._client.get_banks()
            return {"success": True, "banks": banks}
        except Exception as e:
            logger.error("get_banks_failed", error=str(e))
            return {"success": False, "banks": [], "error": str(e)}

    async def resolve_account(self, account_number: str, bank_code: str, currency: str = "NGN") -> dict[str, Any]:
        """Resolve account (Adapter for PaymentProvider interface compatibility)."""
        try:
            resolved = await self.resolve_account_number(account_number, bank_code)
            if resolved:
                return {
                    "success": True,
                    "account_name": resolved.account_name,
                    "account_number": resolved.account_number,
                    "bank_code": resolved.bank_code,
                    "provider": self.provider_name,
                }
            return {
                "success": False,
                "error": "Account not found",
                "provider": self.provider_name,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "provider": self.provider_name,
            }
