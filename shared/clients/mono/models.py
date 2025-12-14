"""Pydantic models for Mono API responses."""

from typing import Optional, List
from pydantic import BaseModel


class MonoApiError(Exception):
    """Mono API error with HTTP status, error code, and message."""
    
    def __init__(
        self,
        http_status: int,
        message: str,
        error_code: Optional[str] = None,
        raw_response: Optional[str] = None
    ):
        self.http_status = http_status
        self.message = message
        self.error_code = error_code
        self.raw_response = raw_response
        super().__init__(f"[{http_status}] {message}")

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.http_status < 500

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.http_status < 600

    @property
    def is_rate_limited(self) -> bool:
        return self.http_status == 429

    @property
    def is_unauthorized(self) -> bool:
        return self.http_status == 401

    @property
    def is_not_found(self) -> bool:
        return self.http_status == 404


class BvnMethod(BaseModel):
    method: str
    hint: str


class Institution(BaseModel):
    name: str
    branch: Optional[str] = None
    bank_code: str


class BvnLookupData(BaseModel):
    session_id: str
    bvn: str
    methods: List[BvnMethod]


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


class TransferDestination(BaseModel):
    """Bank account for mandate authorization transfer."""
    account_number: str
    bank_code: str
    bank_name: str


class MandateData(BaseModel):
    """Mandate creation response."""
    id: str
    status: str
    mandate_type: str
    debit_type: str
    nibss_code: Optional[str] = None
    approved_amount: Optional[int] = None
    amount: int
    account_name: Optional[str] = None
    account_number: str
    bank_code: str
    bank_name: Optional[str] = None
    reference: str
    start_date: str
    end_date: str
    mono_url: Optional[str] = None
    transfer_destinations: Optional[List[TransferDestination]] = None
