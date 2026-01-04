"""Fetch and filter utilities for query execution."""

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from apps.core.src.agent.sub_agents.query.models import Filters, NormalizedQuery, match_category
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string to date object."""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def extract_counterparty(narration: str) -> str:
    """Extract counterparty name from narration."""
    if not narration:
        return "Unknown"

    narration = narration.strip().upper()

    # NIP Transfer pattern: "0000132312091322123456789012345 NIP TRANSFER TO ADEBAYO JAMES"
    if "NIP TRANSFER" in narration or narration.startswith("0000"):
        for direction in ("TO ", "FROM "):
            if direction in narration:
                idx = narration.index(direction) + len(direction)
                name = narration[idx:].strip()
                return name.title()[:25] if name else "Bank Transfer"
        return "Bank Transfer"

    for prefix in ("TRANSFER TO ", "TRANSFER FROM ", "PAYMENT TO ", "FROM ", "TO "):
        if narration.startswith(prefix):
            name = narration[len(prefix) :].strip()
            parts = name.split(" - ")
            return parts[0].title()[:25] if parts[0] else "Transfer"

    if narration.startswith("POS PURCHASE"):
        merchant = narration[14:].strip(" -")
        return merchant.title()[:25] if merchant else "POS Purchase"

    known = {
        "UBER": "Uber",
        "BOLT": "Bolt",
        "TAXIFY": "Bolt",
        "NETFLIX": "Netflix",
        "SPOTIFY": "Spotify",
        "MTN": "MTN",
        "GLO": "Glo",
        "AIRTEL": "Airtel",
        "9MOBILE": "9mobile",
    }
    for key, name in known.items():
        if key in narration:
            return name

    if any(x in narration for x in ("CHARGE", "FEE", "STAMP DUTY", "VAT", "SMS ALERT")):
        return "Bank Charges"

    if "AIRTIME" in narration:
        return "Airtime"

    if "ATM" in narration:
        return "ATM Withdrawal"

    parts = narration.split(" - ")
    result = parts[0].strip().title()
    if len(result) > 25:
        result = result[:22] + "..."
    return result if result else "Unknown"


def apply_filters(transactions: list[dict], filters: Filters) -> list[dict]:
    """Apply filters to transaction list."""
    result = transactions

    if filters.min_amount is not None:
        result = [t for t in result if abs(t.get("amount", 0)) >= filters.min_amount]

    if filters.max_amount is not None:
        result = [t for t in result if abs(t.get("amount", 0)) <= filters.max_amount]

    if filters.transaction_type:
        result = [t for t in result if t.get("type") == filters.transaction_type]

    if filters.category:
        result = [t for t in result if match_category(t.get("narration", ""), filters.category)]

    if filters.merchant:
        result = [t for t in result if any(m.lower() in t.get("narration", "").lower() for m in filters.merchant)]

    if filters.exclude:
        result = [t for t in result if not any(e.lower() in t.get("narration", "").lower() for e in filters.exclude)]

    if filters.account_filter:
        filter_term = filters.account_filter.lower()
        result = [t for t in result if filter_term in t.get("bank_name", "").lower()]

    return result


async def fetch_and_filter(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Fetch transactions and apply filters."""
    if query.time_range:
        start = query.time_range.start.isoformat()
        end = query.time_range.end.isoformat()
    else:
        from datetime import timedelta

        days = 30 if query.intent == "analytics_summary" else 7
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=days)).isoformat()

    bank_map: dict[str, str] = {}
    if accounts_info:
        for acc in accounts_info:
            acc_id = acc.get("account_id") or acc.get("mono_account_id", "")
            bank_name = acc.get("bank_name", "")
            if acc_id and bank_name:
                bank_map[acc_id] = bank_name

    def to_dict(t: Any) -> dict:
        if hasattr(t, "model_dump"):
            return t.model_dump()
        elif is_dataclass(t) and not isinstance(t, type):
            d = asdict(t)
            if "transaction_id" in d:
                d["id"] = d.pop("transaction_id")
            if "transaction_type" in d:
                d["type"] = d.pop("transaction_type")
            return d
        elif isinstance(t, dict):
            return t
        return {"raw": str(t)}

    if query.accounts_scope == "all" and len(account_ids) > 1:
        all_txns: list[dict[str, Any]] = []
        for acc_id in account_ids:
            txns = await provider.get_transactions(acc_id, start_date=start, end_date=end, limit=100)
            for t in txns:
                td = to_dict(t)
                td["bank_name"] = bank_map.get(acc_id, "")
                all_txns.append(td)
        transactions = sorted(all_txns, key=lambda t: t.get("date", ""), reverse=True)
    else:
        txns = await provider.get_transactions(account_id, start_date=start, end_date=end, limit=100)
        transactions = [to_dict(t) for t in txns]

    # --- MERGE LOCAL TRANSACTIONS ---
    if user_id:
        try:
            from shared.repositories.unit_of_work import UnitOfWork

            with UnitOfWork() as uow:
                if uow.transactions:
                    local_txns = uow.transactions.get_by_user(user_id, limit=20)
                    for l_txn in local_txns:
                        # Convert SQL model to dict format matching provider
                        raw_date = l_txn.created_at
                        txn_dict = {
                            "id": str(l_txn.id),
                            "type": "debit" if l_txn.transaction_type == "transfer" else "credit",
                            "amount": l_txn.amount,
                            "narration": l_txn.narration or f"Transfer to {l_txn.recipient_name}",
                            "date": raw_date.isoformat(),
                            "currency": l_txn.currency,
                            "status": l_txn.status,
                            "bank_name": l_txn.source_bank_name or "Wallet",
                        }

                        # Avoid duplicates if provider already returned it (check via amount/date/narration match roughly)
                        # or if we have a transaction_id match.
                        # Simple de-dupe logic: if ID matches any 'id' or 'transaction_id' in transactions list.
                        is_duplicate = False
                        l_prov_id = l_txn.transaction_id
                        for existing in transactions:
                            if l_prov_id and l_prov_id == existing.get("id"):
                                is_duplicate = True
                                break
                            # Fallback fuzzy match for very recent transactions
                            if getattr(l_txn, "amount", 0) == existing.get(
                                "amount"
                            ) and l_txn.narration == existing.get("narration"):
                                is_duplicate = True
                                break

                        if not is_duplicate:
                            transactions.append(txn_dict)

                    # Re-sort mixed list
                    transactions = sorted(transactions, key=lambda t: t.get("date", ""), reverse=True)
        except Exception as e:
            logger.warning("failed_to_merge_local_transactions", error=str(e))

    transactions = [t for t in transactions if start <= t.get("date", "")[:10] <= end]

    if query.filters:
        transactions = apply_filters(transactions, query.filters)

    return transactions
