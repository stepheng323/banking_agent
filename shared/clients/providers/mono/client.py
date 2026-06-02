"""Mono API Client for bank data access."""

from datetime import UTC, datetime
from uuid import uuid4

import aiohttp

from shared.config.settings import settings
from shared.money import require_kobo_to_naira
from shared.utils.logging import get_logger, log_fingerprint

from . import mock_data
from .models import (
    AccountData,
    AccountLookupData,
    BalanceData,
    BankAccount,
    BvnLookupData,
    CustomerData,
    MandateData,
    MonoApiError,
    Transaction,
)

logger = get_logger(__name__)


class MonoClient:
    """Client for interacting with Mono API v2/v3."""

    BASE_URL = "https://api.withmono.com"

    def __init__(self):
        self.use_mock = settings.use_mono_mock
        self.api_key = settings.mono_api_key

    async def lookup_account_number(self, account_number: str, bank_code: str) -> AccountLookupData | None:
        """Resolve account details."""
        if self.use_mock:
            return mock_data.get_mock_account_lookup(account_number, bank_code)

        try:
            body = {"nip_code": bank_code, "account_number": account_number}
            data = await self._request("POST", "/v3/lookup/account-number", body=body)
            if isinstance(data, dict):
                return AccountLookupData(**data)
            return None
        except (MonoApiError, ValueError):
            return None

    async def get_banks(self) -> list[dict]:
        """Fetch list of supported banks."""
        if self.use_mock:
            return mock_data.get_mock_banks()

        try:
            data = await self._request("GET", "/v3/lookup/banks")
            banks_list = data.get("banks", []) if isinstance(data, dict) else []

            mapped_banks = []
            for bank in banks_list:
                mapped_banks.append(
                    {
                        "name": bank.get("name"),
                        "code": bank.get("bank_code"),
                    }
                )

            return mapped_banks
        except MonoApiError:
            return []

    def _headers(self, session_id: str | None = None, real_time: bool = False) -> dict:
        headers = {
            "mono-sec-key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        if session_id:
            headers["x-session-id"] = session_id
        if real_time:
            headers["x-real-time"] = "true"
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        body: dict | None = None,
        session_id: str | None = None,
        real_time: bool = False,
        unwrap_data: bool = True,
    ) -> dict:
        """Make API request. Raises MonoApiError on failure."""
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._headers(session_id, real_time)

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.request(method, url, headers=headers, params=params, json=body) as resp,
            ):
                raw_text = await resp.text()

                try:
                    data = await resp.json() if resp.content_type == "application/json" else {}
                except Exception:
                    data = {}

                if 200 <= resp.status < 300:
                    if isinstance(data, dict):
                        return data.get("data", data) if unwrap_data else data
                    return data

                error_message = data.get("message", "Request failed")
                error_code = data.get("code") or data.get("error_code")

                logger.error(
                    "mono_api_error",
                    http_status=resp.status,
                    endpoint=endpoint,
                    error_code=error_code,
                    message=error_message,
                )
                raise MonoApiError(
                    http_status=resp.status,
                    message=error_message,
                    error_code=error_code,
                    raw_response=raw_text[:500],
                )

        except aiohttp.ClientError as e:
            logger.error("mono_connection_error", endpoint=endpoint, error=str(e))
            raise MonoApiError(http_status=0, message=f"Connection error: {e}", error_code="CONNECTION_ERROR")

    async def initiate_bvn_lookup(self, bvn: str) -> BvnLookupData:
        """Initiate BVN lookup to get verification methods."""
        if self.use_mock:
            return mock_data.get_mock_bvn_lookup(bvn)
        data = await self._request("POST", "/v2/lookup/bvn/initiate", body={"bvn": bvn, "scope": "bank_accounts"})
        return BvnLookupData(**data)

    async def verify_bvn(self, session_id: str, method: str) -> None:
        """Send verification code via selected method."""
        if self.use_mock:
            return
        await self._request("POST", "/v2/lookup/bvn/verify", body={"method": method}, session_id=session_id)

    async def verify_otp(self, session_id: str, otp: str) -> list[BankAccount]:
        """Verify OTP and get bank accounts linked to BVN."""
        if self.use_mock:
            return mock_data.get_mock_bank_accounts()
        data = await self._request("POST", "/v2/lookup/bvn/details", body={"otp": otp}, session_id=session_id)
        return [BankAccount(**acc) for acc in data]

    async def get_account(self, account_id: str) -> AccountData:
        """Get account details."""
        if self.use_mock:
            return mock_data.get_mock_account(account_id)
        data = await self._request("GET", f"/v2/accounts/{account_id}")
        return AccountData(**data)

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceData:
        """Get account balance."""
        if self.use_mock:
            return mock_data.get_mock_balance(account_id)
        raw = await self._request("GET", f"/v2/accounts/{account_id}/balance", real_time=real_time)
        return BalanceData(
            balance_kobo=raw.get("available_balance", 0),
            balance_naira=require_kobo_to_naira(raw.get("available_balance", 0)),
            ledger_balance_kobo=raw.get("ledger_balance", 0),
            ledger_balance_naira=require_kobo_to_naira(raw.get("ledger_balance", 0)),
            currency=raw.get("currency", "NGN"),
            account_id=account_id,
        )

    async def get_transactions(
        self,
        account_id: str,
        start: str | None = None,
        end: str | None = None,
        transaction_type: str | None = None,
        narration: str | None = None,
        limit: int = 50,
        paginate: bool = True,
        real_time: bool = False,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[Transaction]:
        """Fetch transactions for an account."""
        if self.use_mock:
            return mock_data.get_mock_transactions(
                account_id=account_id,
                start=start,
                end=end,
                transaction_type=transaction_type,
                narration=narration,
                limit=limit,
                user_id=user_id,
                mock_account_slot=mock_account_slot,
            )

        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if transaction_type:
            params["type"] = transaction_type
        if narration:
            params["narration"] = narration
        if not paginate:
            params["paginate"] = "false"
        if limit:
            params["limit"] = str(limit)

        raw_data = await self._request(
            "GET", f"/v2/accounts/{account_id}/transactions", params=params, real_time=real_time
        )
        raw_txns = raw_data.get("transactions", raw_data) if isinstance(raw_data, dict) else raw_data
        transactions = [Transaction(**t) for t in raw_txns]

        return transactions[:limit]

    async def get_transactions_page(
        self,
        account_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        page: int = 1,
        real_time: bool = False,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> tuple[list[Transaction], bool, int | None]:
        """Fetch one paginated transaction page for an account."""
        if self.use_mock:
            return mock_data.get_mock_transactions_page(
                account_id=account_id,
                start=start,
                end=end,
                limit=limit,
                page=page,
                user_id=user_id,
                mock_account_slot=mock_account_slot,
            )

        params: dict[str, str] = {"paginate": "true", "limit": str(limit), "page": str(page)}
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        envelope = await self._request(
            "GET",
            f"/v2/accounts/{account_id}/transactions",
            params=params,
            real_time=real_time,
            unwrap_data=False,
        )
        raw_data = envelope.get("data", [])
        raw_txns = raw_data.get("transactions", raw_data.get("data", [])) if isinstance(raw_data, dict) else raw_data
        if not isinstance(raw_txns, list):
            raw_txns = []
        meta = envelope.get("meta", {}) if isinstance(envelope, dict) else {}
        transactions = [Transaction(**t) for t in raw_txns]
        has_more = bool(meta.get("next"))
        next_page = page + 1 if has_more else None
        return transactions, has_more, next_page

    async def create_customer(
        self,
        first_name: str,
        last_name: str,
        phone: str,
        email: str,
        address: str,
        identity_number: str,
        identity_type: str = "bvn",
    ) -> CustomerData:
        """Create a customer in Mono."""
        if self.use_mock:
            return mock_data.get_mock_customer(first_name, last_name, email, address, identity_number, identity_type)

        body = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "address": address,
            "identity": {"type": identity_type, "number": identity_number},
        }
        data = await self._request("POST", "/v2/customers", body=body)
        return CustomerData(**data)

    async def create_mandate(
        self,
        customer_id: str,
        account_number: str,
        bank_code: str,
        amount: int,
        reference: str,
        start_date: str,
        end_date: str,
        debit_type: str = "variable",
        mandate_type: str = "emandate",
        description: str = "Direct debit mandate",
        fee_bearer: str = "customer",
    ) -> MandateData:
        """
        Create a Direct Debit mandate on a customer's bank account.

        Args:
            customer_id: Mono customer ID
            account_number: Bank account number
            bank_code: Bank code (e.g., "011" for First Bank)
            amount: Maximum debit amount in kobo (for variable) or fixed amount per debit
            reference: Unique reference for this mandate
            start_date: Mandate start date (YYYY-MM-DD)
            end_date: Mandate end date (YYYY-MM-DD)
            debit_type: "variable" or "fixed"
            mandate_type: "emandate", "sweep", or "signed"
            description: Description of the mandate
            fee_bearer: "business" or "customer"
        """
        if self.use_mock:
            return mock_data.get_mock_mandate(
                customer_id, account_number, bank_code, amount, reference, start_date, end_date
            )

        body = {
            "customer": customer_id,
            "account_number": account_number,
            "bank_code": bank_code,
            "amount": amount,
            "reference": reference,
            "start_date": start_date,
            "end_date": end_date,
            "debit_type": debit_type,
            "mandate_type": mandate_type,
            "description": description,
            "fee_bearer": fee_bearer,
        }
        data = await self._request("POST", "/v3/payments/mandates", body=body)
        return MandateData(**data)

    async def cancel_mandate(self, mandate_id: str) -> bool:
        """
        Cancel a Direct Debit mandate.

        Args:
            mandate_id: The Mono mandate ID to cancel

        Returns:
            True if successfully cancelled
        """
        if self.use_mock:
            logger.info("mock_cancel_mandate", mandate_id_hash=log_fingerprint(mandate_id))
            return True

        try:
            await self._request("PATCH", f"/v3/payments/mandates/{mandate_id}/cancel")
            logger.info("mandate_cancelled", mandate_id_hash=log_fingerprint(mandate_id))
            return True
        except MonoApiError as e:
            if e.is_not_found:
                logger.warning("mandate_not_found_for_cancel", mandate_id_hash=log_fingerprint(mandate_id))
                return True
            raise

    async def initiate_debit(
        self,
        mandate_id: str,
        amount: int,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> dict:
        """
        Initiate a one-time debit against a mandate.

        Args:
            mandate_id: The Mono mandate ID
            amount: Amount to debit in kobo
            reference: Unique reference for this debit
            narration: Description for the transaction
            beneficiary_account: If provided, funds go directly to this account
                (direct-to-beneficiary mode)
            beneficiary_bank_code: Required if beneficiary_account is provided

        Returns:
            Dict with debit_id and status
        """
        is_direct_to_beneficiary = bool(beneficiary_account and beneficiary_bank_code)

        if self.use_mock:
            now = datetime.now(UTC).isoformat()
            debit_id = f"mock_debit_{uuid4().hex[:12]}"
            mock_status = "successful"
            debit_type = "direct-to-beneficiary" if is_direct_to_beneficiary else "pooling"
            beneficiary = None
            if is_direct_to_beneficiary:
                beneficiary = {
                    "account_number": beneficiary_account,
                    "bank_code": beneficiary_bank_code,
                }
            mock_debit = mock_data.store_mock_debit(
                {
                    "id": debit_id,
                    "mandate": mandate_id,
                    "status": mock_status,
                    "response_code": "00",
                    "reference": reference,
                    "amount": amount,
                    "narration": narration,
                    "debit_type": debit_type,
                    "beneficiary": beneficiary,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            logger.info(
                "mock_initiate_debit",
                mandate_id_hash=log_fingerprint(mandate_id),
                amount=amount,
                reference=reference,
                direct_to_beneficiary=is_direct_to_beneficiary,
                default_status=mock_status,
            )
            return mock_debit

        body = {
            "mandate": mandate_id,
            "amount": amount,
            "reference": reference,
            "narration": narration,
        }

        if is_direct_to_beneficiary:
            body["debit_type"] = "direct-to-beneficiary"
            body["beneficiary"] = {
                "account_number": beneficiary_account,
                "bank_code": beneficiary_bank_code,
            }

        data = await self._request("POST", "/v3/payments/debits/initiate", body=body)
        logger.info(
            "debit_initiated",
            mandate_id_hash=log_fingerprint(mandate_id),
            debit_id=data.get("id"),
            reference=reference,
            direct_to_beneficiary=is_direct_to_beneficiary,
        )
        return data

    async def get_debit_status(self, debit_id: str) -> dict:
        """
        Get the status of a debit transaction.

        Args:
            debit_id: The Mono debit transaction ID

        Returns:
            Dict with current status and details
        """
        if self.use_mock:
            logger.info("mock_get_debit_status", debit_id=debit_id)
            mock_debit = mock_data.get_mock_debit(debit_id)
            if mock_debit is None:
                raise MonoApiError(404, f"Mock debit not found for id {debit_id}", error_code="not_found")

            current_status = str(mock_debit.get("status", "pending")).lower()
            next_status = {
                "pending": "processing",
                "processing": "successful",
            }.get(current_status, current_status)
            updates: dict[str, str] = {"updated_at": datetime.now(UTC).isoformat()}
            current_code = mock_debit.get("response_code")
            if next_status == "successful":
                updates["response_code"] = "00"
            elif next_status == "failed" and current_code in (None, "00"):
                updates["response_code"] = "51"
                updates["message"] = str(mock_debit.get("message") or "Debit failed")
            if next_status != current_status:
                mock_debit = mock_data.update_mock_debit(
                    debit_id,
                    status=next_status,
                    **updates,
                ) or mock_debit
            elif current_status == "failed" and current_code in (None, "00"):
                mock_debit = mock_data.update_mock_debit(debit_id, **updates) or mock_debit
            return mock_debit

        data = await self._request("GET", f"/v3/payments/debits/{debit_id}")
        return data

    async def refund_payment(self, reference: str, source: str | None = None) -> dict:
        """
        Initiate a refund for a Mono payment reference.

        Mono's refund API takes the original payment reference, not the debit id.
        When source is omitted, Mono defaults to refunding from the pending payout.
        """
        if self.use_mock:
            logger.info("mock_refund_payment", reference=reference, source=source or "pending_payout")
            mock_debit = mock_data.get_mock_debit_by_reference(reference)
            if mock_debit is None:
                raise MonoApiError(404, f"Mock debit not found for reference {reference}", error_code="not_found")
            mock_data.update_mock_debit(
                str(mock_debit["id"]),
                status="reversed",
                response_code="00",
                updated_at=datetime.now(UTC).isoformat(),
            )
            return {
                "id": f"mock_refund_{uuid4().hex[:12]}",
                "reference": reference,
                "status": "reversed",
                "response_code": "00",
                "source": source or "pending_payout",
                "amount": mock_debit.get("amount"),
            }

        body = {"reference": reference}
        if source:
            body["source"] = source
        return await self._request("POST", "/v2/payments/refund", body=body)

    async def verify_payment(self, reference: str) -> dict:
        """Verify a Mono payment by its original payment reference."""
        if self.use_mock:
            logger.info("mock_verify_payment", reference=reference)
            mock_debit = mock_data.get_mock_debit_by_reference(reference)
            if mock_debit is None:
                raise MonoApiError(404, f"Mock debit not found for reference {reference}", error_code="not_found")
            return mock_debit

        return await self._request("GET", f"/v2/payments/verify/{reference}")


mono_client = MonoClient()
