"""Mock data for Mono API (development environment)."""

from datetime import datetime, timedelta

from .models import (
    AccountData,
    AccountLookupData,
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


def _days_ago(days: int, hour: int = 12, minute: int = 0) -> str:
    """Generate ISO date string for N days ago."""
    dt = datetime.now() - timedelta(days=days)
    dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
        balance_kobo=3000000,  # 30,000 naira in kobo
        balance_naira=30000.00,
        ledger_balance_kobo=3000000,
        ledger_balance_naira=30000.00,
        currency="NGN",
        account_id=account_id,
        account_name="Samuel Olamide",
        account_number="1234567890",
    )


# Track account ordering to assign different transaction sets
_account_order: list[str] = []


def reset_mock_transaction_state() -> None:
    """Reset the account order tracker - useful between test runs."""
    _account_order.clear()


def get_mock_transactions(
    account_id: str | None = None,
    transaction_type: str | None = None,
    narration: str | None = None,
    limit: int = 50,
) -> list[Transaction]:
    # Determine which account set to use based on order seen
    if account_id:
        if account_id not in _account_order:
            _account_order.append(account_id)
        account_index = _account_order.index(account_id)
    else:
        account_index = 0

    # First account: Set A, Second account: Set B, Third+: empty
    if account_index == 0:
        txns = _get_account_a_transactions()
    elif account_index == 1:
        txns = _get_account_b_transactions()
    else:
        txns = []

    # Merge local DB transactions (transfers made through app)
    local_txns = _get_local_db_transactions()
    if local_txns:
        txns = txns + local_txns
        txns = sorted(txns, key=lambda t: t.date, reverse=True)

    if transaction_type:
        txns = [t for t in txns if t.type == transaction_type]
    if narration:
        txns = [t for t in txns if narration.lower() in t.narration.lower()]

    return txns[:limit]


def _get_local_db_transactions() -> list[Transaction]:
    """Fetch local transactions from DB and convert to Transaction format."""
    try:
        from shared.repositories.unit_of_work import UnitOfWork

        with UnitOfWork() as uow:
            # Get all recent transactions from all users (dev mode)
            db_txns = (
                uow.db.query(uow.transactions.model)
                .filter(uow.transactions.model.status.in_(["completed", "successful", "success"]))
                .order_by(uow.transactions.model.created_at.desc())
                .limit(50)
                .all()
            )

            result = []
            for t in db_txns:
                tx_date = t.created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if t.created_at else ""
                narration = f"Transfer to {t.recipient_name}" if t.recipient_name else t.narration or "Transfer"

                result.append(
                    Transaction(
                        id=str(t.id)[:8],
                        date=tx_date,
                        narration=narration,
                        amount=int(t.amount * 100),  # Convert to kobo
                        type="debit",
                        category="transfer",
                    )
                )
            return result
    except Exception:
        # If DB not available, return empty
        return []


def _get_account_a_transactions() -> list[Transaction]:
    """First Bank transactions - transfers, transport, subscriptions."""
    return [
        Transaction(
            id="txn_001",
            date=_days_ago(3, 10, 30),
            narration="Transfer to Mum",
            amount=5000000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_002",
            date=_days_ago(4, 14, 22),
            narration="0000132312091322123456789012345 NIP TRANSFER TO ADEBAYO JAMES",
            amount=2500000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_003",
            date=_days_ago(4, 14, 22),
            narration="Transfer from OKONKWO CHIDI - Rent payment",
            amount=15000000,
            type="credit",
            category="transfer",
        ),
        # Transport
        Transaction(
            id="txn_004",
            date=_days_ago(5, 18, 45),
            narration="UBER TRIP - Lagos to VI",
            amount=350000,
            type="debit",
            category="transport",
        ),
        Transaction(
            id="txn_005",
            date=_days_ago(5, 18, 45),
            narration="BOLT RIDE - Home to Office",
            amount=280000,
            type="debit",
            category="transport",
        ),
        # POS Payments
        Transaction(
            id="txn_006",
            date=_days_ago(6, 20, 15),
            narration="POS PURCHASE - SHOPRITE IKEJA MALL",
            amount=4500000,
            type="debit",
            category="groceries",
        ),
        Transaction(
            id="txn_007",
            date=_days_ago(6, 20, 15),
            narration="POS PURCHASE - CHICKEN REPUBLIC",
            amount=450000,
            type="debit",
            category="food",
        ),
        # Salary & Income
        Transaction(
            id="txn_008",
            date=_days_ago(7, 10, 0),
            narration="Salary from TechCorp Nigeria Ltd",
            amount=85000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_009",
            date=_days_ago(8, 16, 30),
            narration="Freelance payment - Website Design",
            amount=15000000,
            type="credit",
            category="income",
        ),
        # Subscriptions
        Transaction(
            id="txn_010",
            date=_days_ago(9, 0, 5),
            narration="Netflix Monthly Subscription",
            amount=650000,
            type="debit",
            category="entertainment",
        ),
        Transaction(
            id="txn_011",
            date=_days_ago(10, 0, 2),
            narration="Spotify Premium",
            amount=350000,
            type="debit",
            category="entertainment",
        ),
        Transaction(
            id="txn_012",
            date=_days_ago(11, 0, 1),
            narration="YouTube Premium Family",
            amount=750000,
            type="debit",
            category="entertainment",
        ),
        # Utilities
        Transaction(
            id="txn_013",
            date=_days_ago(12, 11, 20),
            narration="IKEDC PREPAID METER RECHARGE",
            amount=2000000,
            type="debit",
            category="utilities",
        ),
        Transaction(
            id="txn_014",
            date=_days_ago(13, 9, 0),
            narration="MTN DATA BUNDLE - 75GB",
            amount=1500000,
            type="debit",
            category="utilities",
        ),
        Transaction(
            id="txn_015",
            date=_days_ago(14, 15, 45),
            narration="AIRTIME PURCHASE - GLO",
            amount=100000,
            type="debit",
            category="airtime",
        ),
        # Bank charges
        Transaction(
            id="txn_016",
            date=_days_ago(15, 0, 0),
            narration="SMS ALERT CHARGES - NOV 2024",
            amount=5200,
            type="debit",
            category="bank_charges",
        ),
        Transaction(
            id="txn_017",
            date=_days_ago(16, 0, 0),
            narration="CARD MAINTENANCE FEE",
            amount=50000,
            type="debit",
            category="bank_charges",
        ),
        # Food & Delivery
        Transaction(
            id="txn_018",
            date=_days_ago(17, 19, 30),
            narration="JUMIA FOOD - Order #JF789456",
            amount=850000,
            type="debit",
            category="food",
        ),
        Transaction(
            id="txn_019",
            date=_days_ago(18, 13, 15),
            narration="CHOWDECK - Lunch delivery",
            amount=650000,
            type="debit",
            category="food",
        ),
        # More transfers
        Transaction(
            id="txn_020",
            date=_days_ago(19, 16, 0),
            narration="Transfer to ADESANYA KUNLE - Birthday gift",
            amount=5000000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_021",
            date=_days_ago(20, 10, 30),
            narration="Transfer from JOHNSON MARY - Refund",
            amount=3500000,
            type="credit",
            category="transfer",
        ),
        Transaction(
            id="txn_022",
            date=_days_ago(21, 14, 20),
            narration="NIP/OPAY/EMMANUEL OKORO",
            amount=7500000,
            type="debit",
            category="transfer",
        ),
        # Shopping
        Transaction(
            id="txn_023",
            date=_days_ago(22, 17, 0),
            narration="WEB PURCHASE - JUMIA.COM.NG",
            amount=12500000,
            type="debit",
            category="shopping",
        ),
        Transaction(
            id="txn_024",
            date=_days_ago(23, 11, 45),
            narration="POS PURCHASE - SLOT SYSTEMS LTD",
            amount=45000000,
            type="debit",
            category="electronics",
        ),
        # Investment/Savings
        Transaction(
            id="txn_025",
            date=_days_ago(24, 8, 0),
            narration="PIGGYVEST SAVINGS - Auto-save",
            amount=5000000,
            type="debit",
            category="savings",
        ),
        Transaction(
            id="txn_026",
            date=_days_ago(25, 9, 30),
            narration="COWRYWISE - Investment deposit",
            amount=10000000,
            type="debit",
            category="investment",
        ),
        # ATM
        Transaction(
            id="txn_027",
            date=_days_ago(26, 22, 15),
            narration="ATM WITHDRAWAL - VI BRANCH",
            amount=5000000,
            type="debit",
            category="atm",
        ),
        # Additional credits
        Transaction(
            id="txn_028",
            date=_days_ago(27, 12, 0),
            narration="PAYSTACK - Revenue payout",
            amount=25000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_029",
            date=_days_ago(28, 10, 0),
            narration="Interest credited - Q4 2024",
            amount=125000,
            type="credit",
            category="interest",
        ),
        Transaction(
            id="txn_030",
            date=_days_ago(29, 15, 30),
            narration="Transfer from BAKARE FEMI",
            amount=2000000,
            type="credit",
            category="transfer",
        ),
    ]


def _get_account_b_transactions() -> list[Transaction]:
    """Zenith Bank transactions - salary, investments, different merchants."""
    return [
        Transaction(
            id="txn_b01",
            date=_days_ago(3, 10, 30),
            narration="Salary from Acme Corp",
            amount=95000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_b02",
            date=_days_ago(4, 14, 22),
            narration="Transfer to Dad",
            amount=3000000,
            type="debit",
            category="transfer",
        ),
        Transaction(
            id="txn_b03",
            date=_days_ago(5, 18, 45),
            narration="COWRYWISE Auto-Invest",
            amount=5000000,
            type="debit",
            category="investment",
        ),
        Transaction(
            id="txn_b04",
            date=_days_ago(6, 20, 15),
            narration="GLOVO Food Delivery",
            amount=450000,
            type="debit",
            category="food",
        ),
        Transaction(
            id="txn_b05",
            date=_days_ago(7, 10, 0),
            narration="Freelance Payment - Design",
            amount=12000000,
            type="credit",
            category="income",
        ),
        Transaction(
            id="txn_b06",
            date=_days_ago(8, 16, 30),
            narration="TAXIFY/BOLT Ride",
            amount=320000,
            type="debit",
            category="transport",
        ),
        Transaction(
            id="txn_b07",
            date=_days_ago(9, 0, 5),
            narration="Amazon Prime Subscription",
            amount=850000,
            type="debit",
            category="entertainment",
        ),
    ]


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


def get_mock_account_lookup(account_number: str, bank_code: str | None = None) -> AccountLookupData | None:
    """
    Return a deterministic mock account lookup response.

    Maps specific account numbers to specific names for testing.
    Falls back to a generative name for unknown numbers.
    """
    known_accounts = {
        "1234567890": "SAMUEL OLAMIDE NOMO",
        "0123456789": "CHINEDU OKAFOR PETER",
        "0000000000": "JOHN DOE",
        "9999999999": "HIGH VALUE ACCOUNT",
        "0760505261": "TOLU ADEDAYO",
    }

    if account_number == "0000000000":
        return None

    if account_number in known_accounts:
        name = known_accounts[account_number]
    else:
        mock_names = [
            "FATIMA ZAHRA MUSA",
            "EMMANUEL TUNDE BAKARE",
            "GRACE NGOZI ADEBAYO",
            "YUSUF IBRAHIM",
            "MERCY JOHNSON",
        ]
        idx = sum(ord(c) for c in account_number) % len(mock_names)
        name = mock_names[idx]

    return AccountLookupData(
        name=name,
        account_number=account_number,
    )


def get_mock_banks() -> list[dict]:
    """Return a list of mock supported banks."""
    return [
        {"name": "Access Bank", "code": "044"},
        {"name": "Ecobank Nigeria", "code": "050"},
        {"name": "Fidelity Bank", "code": "070"},
        {"name": "First Bank of Nigeria", "code": "011"},
        {"name": "First City Monument Bank", "code": "214"},
        {"name": "Guaranty Trust Bank", "code": "058"},
        {"name": "Heritage Bank", "code": "030"},
        {"name": "Keystone Bank", "code": "082"},
        {"name": "Kuda Bank", "code": "50211"},
        {"name": "Moniepoint Microfinance Bank", "code": "50373"},
        {"name": "OPay", "code": "999991"},
        {"name": "PalmPay", "code": "999992"},
        {"name": "Polaris Bank", "code": "076"},
        {"name": "Providus Bank", "code": "101"},
        {"name": "Stanbic IBTC Bank", "code": "221"},
        {"name": "Standard Chartered Bank", "code": "068"},
        {"name": "Sterling Bank", "code": "232"},
        {"name": "Union Bank of Nigeria", "code": "032"},
        {"name": "United Bank for Africa", "code": "033"},
        {"name": "Unity Bank", "code": "215"},
        {"name": "Wema Bank", "code": "035"},
        {"name": "Zenith Bank", "code": "057"},
    ]
