"""Canonical Mono mock transaction fixtures and query helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import Transaction


def _days_ago(days: int, hour: int = 12, minute: int = 0) -> str:
    """Generate ISO date string for N days ago."""
    dt = datetime.now() - timedelta(days=days)
    dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def account_a_transactions() -> list[Transaction]:
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
        Transaction(
            id="txn_027",
            date=_days_ago(26, 22, 15),
            narration="ATM WITHDRAWAL - VI BRANCH",
            amount=5000000,
            type="debit",
            category="atm",
        ),
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


def account_b_transactions() -> list[Transaction]:
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


def filter_transactions(
    transactions: list[Transaction],
    *,
    start: str | None = None,
    end: str | None = None,
    transaction_type: str | None = None,
    narration: str | None = None,
) -> list[Transaction]:
    """Apply Mono-like transaction filters to the mock dataset."""
    result = list(transactions)

    if start:
        result = [txn for txn in result if txn.date[:10] >= start[:10]]
    if end:
        result = [txn for txn in result if txn.date[:10] <= end[:10]]
    if transaction_type:
        result = [txn for txn in result if txn.type == transaction_type]
    if narration:
        result = [txn for txn in result if narration.lower() in txn.narration.lower()]

    return sorted(result, key=lambda txn: (txn.date, txn.id or ""), reverse=True)


def paginate_transactions(
    transactions: list[Transaction],
    *,
    limit: int,
    page: int,
) -> tuple[list[Transaction], bool, int | None]:
    """Slice a sorted transaction list into pages."""
    page_size = max(limit, 1)
    page_number = max(page, 1)
    start_idx = (page_number - 1) * page_size
    end_idx = start_idx + page_size
    items = transactions[start_idx:end_idx]
    has_more = end_idx < len(transactions)
    return items, has_more, page_number + 1 if has_more else None
