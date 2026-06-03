"""Mock data for Mono API (development environment)."""

from copy import deepcopy
from decimal import Decimal

from .mock_transactions import (
    account_a_transactions,
    account_b_transactions,
    filter_transactions,
    paginate_transactions,
)
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
        balance_naira=Decimal("30000.00"),
        ledger_balance_kobo=3000000,
        ledger_balance_naira=Decimal("30000.00"),
        currency="NGN",
        account_id=account_id,
        account_name="Samuel Olamide",
        account_number="1234567890",
    )


# Track user-scoped account-to-slot assignment for deterministic mock routing.
_user_account_slots: dict[str, dict[str, int]] = {}
_mock_debits_by_id: dict[str, dict] = {}


def reset_mock_transaction_state() -> None:
    """Reset mock account-slot state - useful between test runs."""
    _user_account_slots.clear()
    _mock_debits_by_id.clear()


def store_mock_debit(debit: dict) -> dict:
    """Persist a mock debit payload for later status retrieval."""
    stored = deepcopy(debit)
    _mock_debits_by_id[str(stored["id"])] = stored
    return deepcopy(stored)


def get_mock_debit(debit_id: str) -> dict | None:
    """Return a copy of a stored mock debit payload."""
    debit = _mock_debits_by_id.get(str(debit_id))
    if debit is None:
        return None
    return deepcopy(debit)


def get_mock_debit_by_reference(reference: str) -> dict | None:
    """Return a copy of a stored mock debit payload by payment reference."""
    for debit in _mock_debits_by_id.values():
        if str(debit.get("reference") or "") == str(reference):
            return deepcopy(debit)
    return None


def update_mock_debit(debit_id: str, **updates: object) -> dict | None:
    """Apply partial updates to a stored mock debit payload."""
    debit = _mock_debits_by_id.get(str(debit_id))
    if debit is None:
        return None
    debit.update(updates)
    return deepcopy(debit)


def _normalize_user_key(user_id: str | None) -> str:
    return user_id or "__anonymous__"


def _resolve_mock_account_slot(
    *,
    account_id: str | None,
    user_id: str | None,
    mock_account_slot: int | None,
) -> int:
    """Resolve a deterministic per-user account slot for fixture routing."""
    if mock_account_slot is not None:
        return max(mock_account_slot, 0)

    user_key = _normalize_user_key(user_id)
    account_key = str(account_id or "__default__")
    slots = _user_account_slots.setdefault(user_key, {})
    if account_key not in slots:
        slots[account_key] = len(slots)
    return slots[account_key]


def get_mock_transactions(
    account_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    transaction_type: str | None = None,
    narration: str | None = None,
    limit: int = 50,
    user_id: str | None = None,
    mock_account_slot: int | None = None,
) -> list[Transaction]:
    account_index = _resolve_mock_account_slot(
        account_id=account_id,
        user_id=user_id,
        mock_account_slot=mock_account_slot,
    )

    # First account: Set A, Second account: Set B, Third+: empty
    if account_index == 0:
        txns = account_a_transactions()
    elif account_index == 1:
        txns = account_b_transactions()
    else:
        txns = []

    filtered = filter_transactions(
        txns,
        start=start,
        end=end,
        transaction_type=transaction_type,
        narration=narration,
    )
    return filtered[:limit]


def get_mock_transactions_page(
    account_id: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    transaction_type: str | None = None,
    narration: str | None = None,
    limit: int = 100,
    page: int = 1,
    user_id: str | None = None,
    mock_account_slot: int | None = None,
) -> tuple[list[Transaction], bool, int | None]:
    """Return a paginated Mono-like transaction response."""
    filtered = get_mock_transactions(
        account_id=account_id,
        start=start,
        end=end,
        transaction_type=transaction_type,
        narration=narration,
        limit=10_000,
        user_id=user_id,
        mock_account_slot=mock_account_slot,
    )
    return paginate_transactions(filtered, limit=limit, page=page)


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
    del customer_id
    return MandateData(
        id="mock_mandate_id",
        status="awaiting_authorization",
        mandate_type="emandate",
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
    del bank_code
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
        {"name": "ALAT by WEMA", "code": "035"},
        {"name": "AltBank", "code": "116"},
        {"name": "Citibank Nigeria", "code": "023"},
        {"name": "Ecobank Nigeria", "code": "050"},
        {"name": "Fidelity Bank", "code": "070"},
        {"name": "FCMB", "code": "214"},
        {"name": "First Bank of Nigeria", "code": "011"},
        {"name": "First City Monument Bank", "code": "214"},
        {"name": "Globus Bank", "code": "103"},
        {"name": "Guaranty Trust Bank", "code": "058"},
        {"name": "Heritage Bank", "code": "030"},
        {"name": "Jaiz Bank", "code": "301"},
        {"name": "Keystone Bank", "code": "082"},
        {"name": "Kuda Bank", "code": "50211"},
        {"name": "Lotus Bank", "code": "303"},
        {"name": "Moniepoint Microfinance Bank", "code": "50373"},
        {"name": "Optimus Bank", "code": "107"},
        {"name": "OPay", "code": "999991"},
        {"name": "PalmPay", "code": "999992"},
        {"name": "Parallex Bank", "code": "104"},
        {"name": "Polaris Bank", "code": "076"},
        {"name": "Premium Trust Bank", "code": "105"},
        {"name": "Providus Bank", "code": "101"},
        {"name": "Rand Merchant Bank", "code": "502"},
        {"name": "Stanbic IBTC Bank", "code": "221"},
        {"name": "Standard Chartered Bank", "code": "068"},
        {"name": "Sterling Bank", "code": "232"},
        {"name": "SunTrust Bank", "code": "100"},
        {"name": "TAJ Bank", "code": "302"},
        {"name": "Titan Trust Bank", "code": "102"},
        {"name": "Union Bank of Nigeria", "code": "032"},
        {"name": "United Bank for Africa", "code": "033"},
        {"name": "Unity Bank", "code": "215"},
        {"name": "Wema Bank", "code": "035"},
        {"name": "Zenith Bank", "code": "057"},
    ]
