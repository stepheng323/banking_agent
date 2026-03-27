"""Coverage-aware bank transaction mirror sync for query reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from apps.core.src.agent.graphs.query.services.narration import analyze_transaction_narration
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from shared.clients.abstractions.banking import BankDataProvider, TransactionData, TransactionPageData
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_MIRROR_PROVIDER = "mono"
_PAGE_LIMIT = 100
_RECENT_SYNC_OVERLAP_DAYS = 3


@dataclass(frozen=True)
class MirroredAccountContext:
    """Runtime mapping between linked account metadata and provider account ids."""

    external_account_id: str
    linked_account_id: str
    user_id: str
    bank_name: str
    account_slot: int


def build_mirrored_account_contexts(
    *,
    account_ids: list[str],
    accounts_info: list[dict[str, Any]] | None,
    user_id: str | None,
) -> list[MirroredAccountContext]:
    """Resolve linked-account metadata needed for the durable mirror path."""
    if not user_id or not accounts_info:
        return []

    indexed: dict[str, MirroredAccountContext] = {}
    for account in accounts_info:
        external_id = str(account.get("account_id") or account.get("mono_account_id") or "").strip()
        linked_account_id = str(account.get("id") or "").strip()
        if not external_id or not linked_account_id:
            continue
        indexed[external_id] = MirroredAccountContext(
            external_account_id=external_id,
            linked_account_id=linked_account_id,
            user_id=user_id,
            bank_name=str(account.get("bank_name") or "").strip(),
            account_slot=0,
        )

    resolved = []
    for slot, account_id in enumerate(account_ids):
        if account_id not in indexed:
            continue
        context = indexed[account_id]
        resolved.append(
            MirroredAccountContext(
                external_account_id=context.external_account_id,
                linked_account_id=context.linked_account_id,
                user_id=context.user_id,
                bank_name=context.bank_name,
                account_slot=slot,
            )
        )
    return resolved if len(resolved) == len(account_ids) else []


def _normalize_provider_timestamp(raw_value: str) -> datetime:
    """Normalize provider timestamps to naive UTC for Postgres TIMESTAMP columns."""
    normalized = (raw_value or "").strip()
    if not normalized:
        return datetime.now(UTC).replace(tzinfo=None)

    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(normalized[:19], "%Y-%m-%dT%H:%M:%S")

    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _merge_windows(windows: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping or adjacent coverage windows."""
    normalized = sorted((start, end) for start, end in windows if start <= end)
    if not normalized:
        return []

    merged: list[tuple[date, date]] = [normalized[0]]
    for start, end in normalized[1:]:
        current_start, current_end = merged[-1]
        if start <= current_end + timedelta(days=1):
            merged[-1] = (current_start, max(current_end, end))
        else:
            merged.append((start, end))
    return merged


def _recent_sync_window(
    *,
    start_date: date,
    end_date: date,
    latest_posted_at: datetime | None,
    today: date,
) -> tuple[date, date] | None:
    """Return the recent overlap sync window if the query touches the recent period."""
    recent_threshold = today - timedelta(days=_RECENT_SYNC_OVERLAP_DAYS)
    sync_end = min(end_date, today)
    if sync_end < recent_threshold:
        return None

    anchor = sync_end
    if latest_posted_at is not None:
        anchor = min(sync_end, latest_posted_at.date())
    sync_start = max(start_date, anchor - timedelta(days=_RECENT_SYNC_OVERLAP_DAYS))
    if sync_start > sync_end:
        return None
    return sync_start, sync_end


async def _fetch_provider_page(
    provider: BankDataProvider,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    page: int,
    limit: int,
    user_id: str | None = None,
    mock_account_slot: int | None = None,
) -> TransactionPageData:
    """Fetch a transaction page from the provider with compatibility fallback."""
    page_method = getattr(provider, "get_transactions_page", None)
    if callable(page_method):
        return await page_method(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            page=page,
            user_id=user_id,
            mock_account_slot=mock_account_slot,
        )

    transactions = await provider.get_transactions(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        user_id=user_id,
        mock_account_slot=mock_account_slot,
    )
    has_more = len(transactions) >= limit
    return TransactionPageData(
        transactions=transactions,
        page=page,
        has_more=has_more,
        next_page=page + 1 if has_more else None,
    )


async def _fetch_all_provider_transactions(
    provider: BankDataProvider,
    *,
    account_id: str,
    start_date: date,
    end_date: date,
    user_id: str | None = None,
    mock_account_slot: int | None = None,
) -> list[TransactionData]:
    """Fetch an entire broad coverage window from the provider."""
    fetched: list[TransactionData] = []
    seen_ids: set[str] = set()
    page = 1

    while True:
        page_data = await _fetch_provider_page(
            provider,
            account_id=account_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            page=page,
            limit=_PAGE_LIMIT,
            user_id=user_id,
            mock_account_slot=mock_account_slot,
        )
        for transaction in page_data.transactions:
            dedupe_key = f"{transaction.transaction_id}:{transaction.date}:{transaction.narration}:{transaction.amount}"
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            fetched.append(transaction)

        if not page_data.has_more:
            break
        page = page_data.next_page or (page + 1)

    return fetched


def _mirror_row_from_transaction(
    transaction: TransactionData,
    *,
    account: MirroredAccountContext,
) -> dict[str, Any]:
    """Normalize provider transaction data into a bank-transaction mirror row."""
    posted_at = _normalize_provider_timestamp(transaction.date)
    analysis = analyze_transaction_narration(
        narration=transaction.narration,
        transaction_type=transaction.transaction_type,
        provider_category=transaction.category,
        provider_counterparty=transaction.counterparty,
    )
    provider_transaction_id = str(transaction.transaction_id or "").strip() or (
        f"{posted_at.isoformat()}:{transaction.narration}:{transaction.amount}"
    )
    return {
        "user_id": account.user_id,
        "linked_account_id": account.linked_account_id,
        "provider": _MIRROR_PROVIDER,
        "provider_transaction_id": provider_transaction_id,
        "posted_at": posted_at,
        "posted_date": posted_at.date(),
        "amount": transaction.amount,
        "currency": "NGN",
        "transaction_type": transaction.transaction_type,
        "narration": transaction.narration,
        "category": transaction.category,
        "counterparty": analysis.counterparty,
        "counterparty_role": analysis.counterparty_role,
        "counterparty_source": analysis.counterparty_source,
        "resolved_category": analysis.resolved_category,
        "category_source": analysis.category_source,
        "parser_rule": analysis.parser_rule,
        "bank_name": account.bank_name,
        "raw_payload": {
            "transaction_id": transaction.transaction_id,
            "date": transaction.date,
            "narration": transaction.narration,
            "amount": transaction.amount,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "counterparty": analysis.counterparty,
            "counterparty_role": analysis.counterparty_role,
            "counterparty_source": analysis.counterparty_source,
            "resolved_category": analysis.resolved_category,
            "category_source": analysis.category_source,
            "parser_rule": analysis.parser_rule,
        },
    }


async def ensure_mirror_coverage(
    provider: BankDataProvider,
    *,
    account: MirroredAccountContext,
    start_date: date,
    end_date: date,
    today: date | None = None,
) -> None:
    """Ensure a query window is durable and queryable from the local mirror."""
    from shared.repositories.unit_of_work import UnitOfWork

    today_value = today or lagos_today()

    async with UnitOfWork() as uow:
        if uow.bank_transactions is None or uow.bank_transaction_coverages is None or uow.accounts is None:
            raise RuntimeError("bank_transaction_mirror_repositories_unavailable")

        missing_windows = await uow.bank_transaction_coverages.find_missing_gaps(
            account.linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=_MIRROR_PROVIDER,
        )
        latest_posted_at = await uow.bank_transactions.get_latest_posted_at(
            account.linked_account_id,
            provider=_MIRROR_PROVIDER,
        )
        recent_window = _recent_sync_window(
            start_date=start_date,
            end_date=end_date,
            latest_posted_at=latest_posted_at,
            today=today_value,
        )
        sync_windows = list(missing_windows)
        if recent_window is not None:
            sync_windows.append(recent_window)

        for window_start, window_end in _merge_windows(sync_windows):
            provider_transactions = await _fetch_all_provider_transactions(
                provider,
                account_id=account.external_account_id,
                start_date=window_start,
                end_date=window_end,
                user_id=account.user_id,
                mock_account_slot=account.account_slot,
            )
            await uow.bank_transactions.bulk_upsert(
                [_mirror_row_from_transaction(txn, account=account) for txn in provider_transactions]
            )
            await uow.bank_transaction_coverages.add_full_coverage(
                account.linked_account_id,
                start_date=window_start,
                end_date=window_end,
                provider=_MIRROR_PROVIDER,
            )

        linked_account = await uow.accounts.get_by_account_id(account.external_account_id)
        if linked_account is not None:
            extra_data = dict(linked_account.extra_data or {})
            extra_data["bank_transaction_sync"] = {
                "provider": _MIRROR_PROVIDER,
                "last_recent_sync_at": datetime.now(UTC).isoformat(),
                "coverage_checked_start": start_date.isoformat(),
                "coverage_checked_end": end_date.isoformat(),
            }
            linked_account.extra_data = extra_data
            uow.db.add(linked_account)
            await uow.db.flush()

        is_covered = await uow.bank_transaction_coverages.is_window_covered(
            account.linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=_MIRROR_PROVIDER,
        )
        if not is_covered:
            raise RuntimeError(
                f"bank_transaction_window_uncovered:{account.external_account_id}:{start_date.isoformat()}:{end_date.isoformat()}"
            )


async def load_mirrored_transactions(
    *,
    account_contexts: list[MirroredAccountContext],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Load mirrored bank transactions for linked accounts from Postgres."""
    from shared.repositories.unit_of_work import UnitOfWork

    if not account_contexts:
        return []

    async with UnitOfWork() as uow:
        if uow.bank_transactions is None:
            raise RuntimeError("bank_transaction_repository_unavailable")

        rows = await uow.bank_transactions.list_by_accounts_window(
            [account.linked_account_id for account in account_contexts],
            start_date=start_date,
            end_date=end_date,
            provider=_MIRROR_PROVIDER,
        )

    linked_to_external = {
        account.linked_account_id: account.external_account_id
        for account in account_contexts
    }

    return [
        {
            "id": row.provider_transaction_id,
            "transaction_id": row.provider_transaction_id,
            "type": row.transaction_type,
            "amount": row.amount,
            "narration": row.narration or "",
            "date": row.posted_at.isoformat(),
            "category": row.category,
            "counterparty": getattr(row, "counterparty", None),
            "counterparty_role": getattr(row, "counterparty_role", None),
            "counterparty_source": getattr(row, "counterparty_source", None),
            "resolved_category": row.resolved_category,
            "category_source": row.category_source,
            "parser_rule": getattr(row, "parser_rule", None),
            "bank_name": row.bank_name or "",
            "currency": row.currency,
            "source_account_id": linked_to_external.get(str(row.linked_account_id)),
            "source_account_label": row.bank_name or "",
        }
        for row in rows
    ]
