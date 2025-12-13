"""Mono API Client for bank data access."""

from typing import Optional, List
from pydantic import BaseModel
import aiohttp

from shared.utils.logging import get_logger
from shared.config.settings import settings

logger = get_logger(__name__)


class MonoApiError(Exception):
    """Mono API error with status and message."""
    def __init__(self, status: str, message: str, error: Optional[str] = None):
        self.status = status
        self.message = message
        self.error = error
        super().__init__(message)


class BvnMethod(BaseModel):
    method: str
    hint: str


class BvnLookupData(BaseModel):
    session_id: str
    bvn: str
    methods: List[BvnMethod]


class Institution(BaseModel):
    name: str
    branch: Optional[str] = None
    bank_code: str


class BankAccount(BaseModel):
    account_name: str
    account_number: str
    account_type: str
    account_designation: Optional[str] = None
    institution: Institution


class BalanceData(BaseModel):
    balance_kobo: int
    balance_naira: float
    ledger_balance_kobo: int
    ledger_balance_naira: float
    currency: str
    account_id: str
    account_name: Optional[str] = None
    account_number: Optional[str] = None


class Transaction(BaseModel):
    id: Optional[str] = None
    date: str
    narration: str
    amount: int
    type: str
    category: Optional[str] = None


class CustomerData(BaseModel):
    id: str
    name: str
    first_name: str
    last_name: str
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    identification_no: str
    identification_type: str
    bvn: str


class AccountData(BaseModel):
    id: str
    name: str
    account_number: str
    type: str
    institution: Institution


class MonoClient:
    """Client for interacting with Mono API v2."""

    def __init__(self):
        self.use_mock = settings.app_env == "development"
        self.base_url = "https://api.withmono.com"
        self.api_key = settings.mono_api_key
        self.headers = {
            "mono-sec-key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        session_id: Optional[str] = None,
        real_time: bool = False
    ) -> dict:
        """Make an API request to Mono. Raises MonoApiError on failure."""
        url = f"{self.base_url}{endpoint}"
        headers = self.headers.copy()
        
        if real_time:
            headers["x-real-time"] = "true"
        if session_id:
            headers["x-session-id"] = session_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=body if body else None
                ) as resp:
                    data = await resp.json() if resp.content_type == "application/json" else {}
                    
                    if resp.status == 200:
                        return data.get("data", data)
                    else:
                        error_text = await resp.text()
                        logger.error("mono_api_error", status=resp.status, endpoint=endpoint, error=error_text[:200])
                        raise MonoApiError(
                            status="error",
                            message=data.get("message", "Request failed"),
                            error=error_text
                        )
        except aiohttp.ClientError as e:
            logger.error("mono_connection_error", endpoint=endpoint, error=str(e))
            raise MonoApiError(status="error", message="Connection error", error=str(e))

    async def initiate_bvn_lookup(self, bvn: str) -> BvnLookupData:
        """Initiate BVN lookup to get verification methods."""
        if self.use_mock:
            return BvnLookupData(
                session_id="74c8fe70-ea2c-458e-a99f-3f7a6061632c",
                bvn=bvn,
                methods=[
                    BvnMethod(method="email", hint="An email with a verification code will be sent to tomi***jr@gmail.com"),
                    BvnMethod(method="phone", hint="Sms with a verification code will be sent to phone 0818***6496"),
                ],
            )

        data = await self._request("POST", "/v2/lookup/bvn/initiate", body={"bvn": bvn, "scope": "bank_accounts"})
        return BvnLookupData(**data)

    async def verify_bvn(self, session_id: str, method: str) -> None:
        """Send verification code via selected method."""
        if self.use_mock:
            return None

        await self._request("POST", "/v2/lookup/bvn/verify", body={"method": method}, session_id=session_id)

    async def verify_otp(self, session_id: str, otp: str) -> List[BankAccount]:
        """Verify OTP and get bank accounts linked to BVN."""
        if self.use_mock:
            return [
                BankAccount(
                    account_name="Samuel Olamide",
                    account_number="1234567890",
                    account_type="SAVINGS",
                    account_designation="OTHERS",
                    institution=Institution(name="First Bank", branch="4659818", bank_code="011"),
                ),
                BankAccount(
                    account_name="Olamide Samuel",
                    account_number="1234509384",
                    account_type="CURRENT",
                    account_designation="OTHERS",
                    institution=Institution(name="Zenith Bank", branch="4661483", bank_code="057"),
                ),
            ]

        data = await self._request("POST", "/v2/lookup/bvn/details", body={"otp": otp}, session_id=session_id)
        return [BankAccount(**acc) for acc in data]

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceData:
        """Get account balance."""
        if self.use_mock:
            return BalanceData(
                balance_kobo=50000000,
                balance_naira=500000.00,
                ledger_balance_kobo=50000000,
                ledger_balance_naira=500000.00,
                currency="NGN",
                account_id=account_id,
                account_name="Samuel Olamide",
                account_number="1234567890",
            )

        raw = await self._request("GET", f"/v2/accounts/{account_id}/balance", real_time=real_time)
        return BalanceData(
            balance_kobo=raw.get("available_balance", 0),
            balance_naira=raw.get("available_balance", 0) / 100,
            ledger_balance_kobo=raw.get("ledger_balance", 0),
            ledger_balance_naira=raw.get("ledger_balance", 0) / 100,
            currency=raw.get("currency", "NGN"),
            account_id=account_id,
        )

    async def get_transactions(
        self,
        account_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        transaction_type: Optional[str] = None,
        narration: Optional[str] = None,
        limit: int = 50,
        paginate: bool = True,
        real_time: bool = False
    ) -> List[Transaction]:
        """Fetch transactions for an account."""
        if self.use_mock:
            mock_txns = [
                Transaction(id="txn_001", date="2024-12-10", narration="Transfer to Mum", amount=5000000, type="debit", category="transfer"),
                Transaction(id="txn_002", date="2024-12-09", narration="Uber trip", amount=350000, type="debit", category="transport"),
                Transaction(id="txn_003", date="2024-12-08", narration="Salary from Company Ltd", amount=35000000, type="credit", category="income"),
                Transaction(id="txn_004", date="2024-12-07", narration="Netflix subscription", amount=500000, type="debit", category="entertainment"),
                Transaction(id="txn_005", date="2024-12-06", narration="Transfer from John", amount=10000000, type="credit", category="transfer"),
            ]
            
            if transaction_type:
                mock_txns = [t for t in mock_txns if t.type == transaction_type]
            if narration:
                mock_txns = [t for t in mock_txns if narration.lower() in t.narration.lower()]
            
            return mock_txns[:limit]

        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if transaction_type:
            params["type"] = transaction_type
        if not paginate:
            params["paginate"] = "false"
        if limit:
            params["limit"] = str(limit)

        raw_data = await self._request("GET", f"/v2/accounts/{account_id}/transactions", params=params, real_time=real_time)
        raw_txns = raw_data.get("transactions", raw_data) if isinstance(raw_data, dict) else raw_data
        
        transactions = [Transaction(**t) for t in raw_txns]
        
        if narration:
            transactions = [t for t in transactions if narration.lower() in t.narration.lower()]
        
        return transactions[:limit]

    async def get_account(self, account_id: str) -> AccountData:
        """Get account details."""
        if self.use_mock:
            return AccountData(
                id=account_id,
                name="Samuel Olamide",
                account_number="1234567890",
                type="SAVINGS",
                institution=Institution(name="First Bank", bank_code="011"),
            )

        data = await self._request("GET", f"/v2/accounts/{account_id}")
        return AccountData(**data)

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
        """
        Create a customer in Mono.
        
        Args:
            first_name: Customer's first name
            last_name: Customer's last name
            phone: Customer's phone number
            email: Customer's email address
            address: Customer's address
            identity_type: Type of identity (bvn, nin)
            identity_number: Identity number (e.g., BVN)
        """
        if self.use_mock:
            return CustomerData(
                id="mock_customer_id",
                name=f"{first_name} {last_name}",
                first_name=first_name,
                last_name=last_name,
                email=email,
                address_line_1=address,
                identification_no=identity_number,
                identification_type=identity_type,
            )

        body = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "address": address,
            "identity": {
                "type": identity_type,
                "number": identity_number,
            },
        }

        data = await self._request("POST", "/v2/customers", body=body)
        return CustomerData(**data)


mono_client = MonoClient()