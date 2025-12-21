"""Mock data for Mono API (development environment)."""

from .models import (
    BvnMethod, BvnLookupData, Institution, BankAccount,
    BalanceData, Transaction, CustomerData, AccountData,
    MandateData, TransferDestination
)


def get_mock_bvn_lookup(bvn: str) -> BvnLookupData:
    return BvnLookupData(
        session_id="74c8fe70-ea2c-458e-a99f-3f7a6061632c",
        bvn=bvn,
        methods=[
            BvnMethod(method="email", hint="An email with a verification code will be sent to tomi***jr@gmail.com"),
            BvnMethod(method="phone", hint="Sms with a verification code will be sent to phone 0818***6496"),
        ],
    )


def get_mock_bank_accounts() -> list[BankAccount]:
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


def get_mock_balance(account_id: str) -> BalanceData:
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


def get_mock_transactions(
    transaction_type: str | None = None,
    narration: str | None = None,
    limit: int = 50
) -> list[Transaction]:
    txns = [
        Transaction(id="txn_001", date="2024-12-10", narration="Transfer to Mum", amount=5000000, type="debit", category="transfer"),
        Transaction(id="txn_002", date="2024-12-09", narration="Uber trip", amount=350000, type="debit", category="transport"),
        Transaction(id="txn_003", date="2024-12-08", narration="Salary from Company Ltd", amount=35000000, type="credit", category="income"),
        Transaction(id="txn_004", date="2024-12-07", narration="Netflix subscription", amount=500000, type="debit", category="entertainment"),
        Transaction(id="txn_005", date="2024-12-06", narration="Transfer from John", amount=10000000, type="credit", category="transfer"),
    ]
    
    if transaction_type:
        txns = [t for t in txns if t.type == transaction_type]
    if narration:
        txns = [t for t in txns if narration.lower() in t.narration.lower()]
    
    return txns[:limit]


def get_mock_account(account_id: str) -> AccountData:
    return AccountData(
        id=account_id,
        name="Samuel Olamide",
        account_number="1234567890",
        type="SAVINGS",
        institution=Institution(name="First Bank", bank_code="011"),
    )


def get_mock_customer(
    first_name: str,
    last_name: str,
    email: str,
    address: str,
    identity_number: str,
    identity_type: str
) -> CustomerData:
    return CustomerData(
        id="mock_customer_id",
        name=f"{first_name} {last_name}",
        first_name=first_name,
        last_name=last_name,
        email=email,
        address_line_1=address,
        identification_no=identity_number,
        identification_type=identity_type,
        bvn=identity_number if identity_type == "bvn" else "",
    )


def get_mock_mandate(
    customer_id: str,
    account_number: str,
    bank_code: str,
    amount: int,
    reference: str,
    start_date: str,
    end_date: str
) -> MandateData:
    return MandateData(
        id="mock_mandate_id",
        status="awaiting_authorization",
        mandate_type="e-mandate",
        debit_type="variable",
        nibss_code="NIBSS123456789",
        amount=amount,
        account_number=account_number,
        bank_code=bank_code,
        bank_name="First Bank",
        reference=reference,
        start_date=start_date,
        end_date=end_date,
        mono_url="https://mono.co/authorize/mock_mandate_id",
        transfer_destinations=[
            TransferDestination(account_number="0123456789", bank_code="070", bank_name="Fidelity Bank"),
            TransferDestination(account_number="9876543210", bank_code="999", bank_name="Paystack-Titan"),
        ],
    )
