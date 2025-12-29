"""Mock data for Mono API (development environment)."""

from .models import (
    AccountData,
    BalanceData,
    BankAccount,
    BvnLookupData,
    BvnMethod,
    CustomerData,
    Institution,
    MandateData,
    Transaction,
    TransferDestination,
)


def get_mock_bvn_lookup(bvn: str) -> BvnLookupData:
    return BvnLookupData(
        session_id="74c8fe70-ea2c-458e-a99f-3f7a6061632c",
        bvn=bvn,
        methods=[
            BvnMethod(
                method="email",
                hint="An email with a verification code will be sent to tomi***jr@gmail.com",
            ),
            BvnMethod(
                method="phone",
                hint="Sms with a verification code will be sent to phone 0818***6496",
            ),
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
    transaction_type: str | None = None, narration: str | None = None, limit: int = 50
) -> list[Transaction]:
    txns = [
        # Recent transfers
        Transaction(
            id="txn_001",
            date="2024-12-28T10:30:00.000Z",
            narration="Transfer to Mum",
            amount=5000000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_002",
            date="2024-12-27T14:22:00.000Z",
            narration="0000132312091322123456789012345 NIP TRANSFER TO ADEBAYO JAMES",
            amount=2500000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_003",
            date="2024-12-27T09:15:00.000Z",
            narration="Transfer from OKONKWO CHIDI - Rent payment",
            amount=15000000,
            type="credit",
            category="transfer",
        ),
        # Transport
        Transaction(
            id="txn_004",
            date="2024-12-26T18:45:00.000Z",
            narration="UBER TRIP - Lagos to VI",
            amount=350000,
            type="debit",
            category="transport",
        ),
        Transaction(
            id="txn_005",
            date="2024-12-26T08:30:00.000Z",
            narration="BOLT RIDE - Home to Office",
            amount=280000,
            type="debit",
            category="transport",
        ),
        # POS Payments
        Transaction(
            id="txn_006",
            date="2024-12-25T20:15:00.000Z",
            narration="POS PURCHASE - SHOPRITE IKEJA MALL",
            amount=4500000,
            type="debit",
            category="groceries",
        ),
        Transaction(
            id="txn_007",
            date="2024-12-25T13:00:00.000Z",
            narration="POS PURCHASE - CHICKEN REPUBLIC",
            amount=450000,
            type="debit",
            category="food",
        ),
        # Salary & Income
        Transaction(
            id="txn_008",
            date="2024-12-24T10:00:00.000Z",
            narration="Salary from TechCorp Nigeria Ltd",
            amount=85000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_009",
            date="2024-12-23T16:30:00.000Z",
            narration="Freelance payment - Website Design",
            amount=15000000,
            type="credit",
            category="income",
        ),
        # Subscriptions
        Transaction(
            id="txn_010",
            date="2024-12-22T00:05:00.000Z",
            narration="Netflix Monthly Subscription",
            amount=650000,
            type="debit",
            category="entertainment",
        ),
        Transaction(
            id="txn_011",
            date="2024-12-21T00:02:00.000Z",
            narration="Spotify Premium",
            amount=350000,
            type="debit",
            category="entertainment",
        ),
        Transaction(
            id="txn_012",
            date="2024-12-20T00:01:00.000Z",
            narration="YouTube Premium Family",
            amount=750000,
            type="debit",
            category="entertainment",
        ),
        # Utilities
        Transaction(
            id="txn_013",
            date="2024-12-19T11:20:00.000Z",
            narration="IKEDC PREPAID METER RECHARGE",
            amount=2000000,
            type="debit",
            category="utilities",
        ),
        Transaction(
            id="txn_014",
            date="2024-12-18T09:00:00.000Z",
            narration="MTN DATA BUNDLE - 75GB",
            amount=1500000,
            type="debit",
            category="utilities",
        ),
        Transaction(
            id="txn_015",
            date="2024-12-17T15:45:00.000Z",
            narration="AIRTIME PURCHASE - GLO",
            amount=100000,
            type="debit",
            category="airtime",
        ),
        # Bank charges
        Transaction(
            id="txn_016",
            date="2024-12-16T00:00:00.000Z",
            narration="SMS ALERT CHARGES - NOV 2024",
            amount=5200,
            type="debit",
            category="bank_charges",
        ),
        Transaction(
            id="txn_017",
            date="2024-12-15T00:00:00.000Z",
            narration="CARD MAINTENANCE FEE",
            amount=50000,
            type="debit",
            category="bank_charges",
        ),
        # Food & Delivery
        Transaction(
            id="txn_018",
            date="2024-12-14T19:30:00.000Z",
            narration="JUMIA FOOD - Order #JF789456",
            amount=850000,
            type="debit",
            category="food",
        ),
        Transaction(
            id="txn_019",
            date="2024-12-13T13:15:00.000Z",
            narration="CHOWDECK - Lunch delivery",
            amount=650000,
            type="debit",
            category="food",
        ),
        # More transfers
        Transaction(
            id="txn_020",
            date="2024-12-12T16:00:00.000Z",
            narration="Transfer to ADESANYA KUNLE - Birthday gift",
            amount=5000000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_021",
            date="2024-12-11T10:30:00.000Z",
            narration="Transfer from JOHNSON MARY - Refund",
            amount=3500000,
            type="credit",
            category="transfer",
        ),
        Transaction(
            id="txn_022",
            date="2024-12-10T14:20:00.000Z",
            narration="NIP/OPAY/EMMANUEL OKORO",
            amount=7500000,
            type="debit",
            category="transfer",
        ),
        # Shopping
        Transaction(
            id="txn_023",
            date="2024-12-09T17:00:00.000Z",
            narration="WEB PURCHASE - JUMIA.COM.NG",
            amount=12500000,
            type="debit",
            category="shopping",
        ),
        Transaction(
            id="txn_024",
            date="2024-12-08T11:45:00.000Z",
            narration="POS PURCHASE - SLOT SYSTEMS LTD",
            amount=45000000,
            type="debit",
            category="electronics",
        ),
        # Investment/Savings
        Transaction(
            id="txn_025",
            date="2024-12-07T08:00:00.000Z",
            narration="PIGGYVEST SAVINGS - Auto-save",
            amount=5000000,
            type="debit",
            category="savings",
        ),
        Transaction(
            id="txn_026",
            date="2024-12-06T09:30:00.000Z",
            narration="COWRYWISE - Investment deposit",
            amount=10000000,
            type="debit",
            category="investment",
        ),
        # ATM
        Transaction(
            id="txn_027",
            date="2024-12-05T22:15:00.000Z",
            narration="ATM WITHDRAWAL - VI BRANCH",
            amount=5000000,
            type="debit",
            category="atm",
        ),
        # Additional credits
        Transaction(
            id="txn_028",
            date="2024-12-04T12:00:00.000Z",
            narration="PAYSTACK - Revenue payout",
            amount=25000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_029",
            date="2024-12-03T10:00:00.000Z",
            narration="Interest credited - Q4 2024",
            amount=125000,
            type="credit",
            category="interest",
        ),
        Transaction(
            id="txn_030",
            date="2024-12-02T15:30:00.000Z",
            narration="Transfer from BAKARE FEMI",
            amount=2000000,
            type="credit",
            category="transfer",
        ),
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
    identity_type: str,
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
    end_date: str,
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
