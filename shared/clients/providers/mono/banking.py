"""Mono implementation of BankDataProvider."""

from shared.clients.abstractions.banking import (
    AccountData,
    BalanceData,
    BankDataProvider,
    BvnLookupResult,
    BvnVerificationResult,
    TransactionData,
    TransactionPageData,
)
from shared.clients.providers.mono.client import MonoClient
from shared.clients.providers.mono.models import MonoApiError
from shared.config.settings import settings
from shared.money import require_kobo_to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MonoBankingProvider(BankDataProvider):
    """Mono implementation of BankDataProvider."""

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
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[TransactionData]:
        try:
            txns = await self._client.get_transactions(
                account_id=account_id,
                start=start_date,
                end=end_date,
                transaction_type=transaction_type,
                limit=limit,
                user_id=user_id,
                mock_account_slot=mock_account_slot,
            )
            return [
                TransactionData(
                    transaction_id=t.id,
                    date=t.date,
                    narration=t.narration,
                    amount=require_kobo_to_naira(t.amount if t.type == "credit" else -t.amount),
                    transaction_type=t.type,
                    category=t.category,
                    counterparty=t.counterparty,
                )
                for t in txns
            ]
        except MonoApiError as e:
            logger.error("get_transactions_failed", account_id=account_id, error=str(e))
            return []

    async def get_transactions_page(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        page: int = 1,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> TransactionPageData:
        """Fetch one paginated page of transactions."""
        try:
            txns, has_more, next_page = await self._client.get_transactions_page(
                account_id=account_id,
                start=start_date,
                end=end_date,
                limit=limit,
                page=page,
                user_id=user_id,
                mock_account_slot=mock_account_slot,
            )
            return TransactionPageData(
                transactions=[
                    TransactionData(
                        transaction_id=t.id,
                        date=t.date,
                        narration=t.narration,
                        amount=require_kobo_to_naira(t.amount if t.type == "credit" else -t.amount),
                        transaction_type=t.type,
                        category=t.category,
                        counterparty=t.counterparty,
                    )
                    for t in txns
                ],
                page=page,
                has_more=has_more,
                next_page=next_page,
            )
        except MonoApiError as e:
            logger.error("get_transactions_page_failed", account_id=account_id, error=str(e))
            return TransactionPageData(transactions=[], page=page, has_more=False, next_page=None)

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
