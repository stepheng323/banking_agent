"""Existence answer copy for transaction query results."""

from apps.chat.src.agent.workers.query.models.domain import QueryExecutionContract, QueryResult
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from banking.presentation.formatters.currency import format_naira


def build_existence_answer(
    result: QueryResult,
    *,
    query_contract: QueryExecutionContract,
) -> str:
    """Build direct yes/no copy for transaction existence questions."""
    items = result.items or []
    count = len(items)
    filters = query_contract.filters
    tx_type = filters.transaction_type if filters else None
    counterparty = _first_filter_value(filters.counterparty if filters else None)
    merchant = _first_filter_value(filters.merchant if filters else None)
    category = _first_filter_value(filters.category if filters else None)
    target = counterparty or merchant or category
    target_phrase = _target_phrase(target, category=category)
    time_phrase = _time_phrase(query_contract)

    if count == 0:
        return _build_existence_no_match(tx_type=tx_type, target_phrase=target_phrase, time_phrase=time_phrase)

    total = sum(abs(float(item.amount)) for item in items)
    amount = format_naira(total)
    if tx_type == "credit":
        if target_phrase:
            if count == 1:
                return f"Yes. You received {amount} from {target_phrase}{time_phrase}."
            return f"Yes. I found {count} credits from {target_phrase}{time_phrase}, totaling {amount}."
        if count == 1:
            return f"Yes. You received {amount}{time_phrase}."
        return f"Yes. I found {count} credits{time_phrase}, totaling {amount}."
    if tx_type == "debit":
        if target_phrase:
            if category:
                if count == 1:
                    return f"Yes. You spent {amount} on {target_phrase}{time_phrase}."
                return f"Yes. I found {count} payments for {target_phrase}{time_phrase}, totaling {amount}."
            if count == 1:
                return f"Yes. You sent {amount} to {target_phrase}{time_phrase}."
            return f"Yes. I found {count} payments to {target_phrase}{time_phrase}, totaling {amount}."
        if count == 1:
            return f"Yes. You spent {amount}{time_phrase}."
        return f"Yes. I found {count} debits{time_phrase}, totaling {amount}."
    if target_phrase:
        if count == 1:
            return f"Yes. I found one transaction with {target_phrase}{time_phrase} for {amount}."
        return f"Yes. I found {count} transactions with {target_phrase}{time_phrase}, totaling {amount}."
    if count == 1:
        return f"Yes. I found one transaction{time_phrase} for {amount}."
    return f"Yes. I found {count} transactions{time_phrase}, totaling {amount}."


def _build_existence_no_match(*, tx_type: str | None, target_phrase: str | None, time_phrase: str) -> str:
    if tx_type == "credit":
        if target_phrase:
            return f"No. I don't see a matching credit from {target_phrase}{time_phrase}."
        return f"No. I don't see a matching credit{time_phrase}."
    if tx_type == "debit":
        if target_phrase:
            return f"No. I don't see any payment to {target_phrase}{time_phrase}."
        return f"No. I don't see a matching debit{time_phrase}."
    if target_phrase:
        return f"No. I don't see a matching transaction with {target_phrase}{time_phrase}."
    return f"No. I don't see a matching transaction{time_phrase}."


def _target_phrase(target: str | None, *, category: str | None) -> str | None:
    if not target:
        return None
    cleaned = target.strip()
    if not cleaned:
        return None
    if category:
        return cleaned.replace("_", " ")
    return cleaned


def _time_phrase(query_contract: QueryExecutionContract) -> str:
    time_range = query_contract.time_range
    if time_range is None:
        return ""
    today = lagos_today()
    if time_range.start == time_range.end == today:
        return " today"
    yesterday = today.fromordinal(today.toordinal() - 1)
    if time_range.start == time_range.end == yesterday:
        return " yesterday"
    if (
        time_range.start.day == 1
        and time_range.end == today
        and time_range.start.year == today.year
        and time_range.start.month == today.month
    ):
        return " this month"
    if time_range.start == today.fromordinal(today.toordinal() - today.weekday()) and time_range.end == today:
        return " this week"
    return " in that period"


def _first_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None
