"""Fetch and filter utilities for query execution."""

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from time import perf_counter
from typing import Any, cast

from apps.chat.src.agent.shared.unified_transactions import UnifiedTransactionService
from apps.chat.src.agent.workers.query.models.domain import (
    Filters,
    QueryExecutionContract,
    match_transaction_category,
)
from apps.chat.src.agent.workers.query.services.analysis.narration import analyze_transaction_narration
from apps.chat.src.agent.workers.query.services.fetching.bank_transaction_mirror import (
    build_mirrored_account_contexts,
    ensure_mirror_coverage,
    load_mirrored_transactions,
)
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.banking import BankDataProvider
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_MULTI_ACCOUNT_FETCH_CONCURRENCY = 4
_MIRROR_SCHEMA_AVAILABLE = True
_MIRROR_MISSING_TABLE_MARKERS = (
    'relation "bank_transaction_coverage" does not exist',
    'relation "bank_transactions" does not exist',
    "UndefinedTableError",
)


def _is_missing_mirror_table_error(exc: Exception) -> bool:
    message = str(exc)
    return any(marker in message for marker in _MIRROR_MISSING_TABLE_MARKERS)


@dataclass(frozen=True)
class TransactionCacheReuseDecision:
    """Cache reuse decision for transaction-list follow-ups.

    Semantics:
    - `exact` reuse applies to filter refinements over the same full fetch envelope.
    - `time_subset` reuse applies to narrower time windows within the same scope.
    - wider windows or stale/mismatched cache snapshots must refetch.
    """

    can_reuse: bool
    strategy: str


def _account_display_label(account: dict[str, Any]) -> str:
    """Return the best available user-facing label for a source account."""
    bank_name = str(account.get("bank_name") or "").strip()
    if bank_name:
        return bank_name

    account_number = str(account.get("account_number") or "").strip()
    if account_number:
        return account_number

    account_id = str(account.get("account_id") or account.get("mono_account_id") or "").strip()
    if account_id:
        return account_id

    return "unknown account"


def _attach_source_account_metadata(
    transaction: dict[str, Any],
    *,
    account_id: str | None,
    account_label: str | None,
    account_number: str | None = None,
) -> dict[str, Any]:
    """Attach source-account metadata while preserving any existing fields."""
    resolved_account_id = str(account_id or transaction.get("source_account_id") or "").strip()
    resolved_label = str(account_label or transaction.get("source_account_label") or "").strip()

    if resolved_account_id:
        transaction["source_account_id"] = resolved_account_id
    if resolved_label:
        transaction["source_account_label"] = resolved_label
    if resolved_label and not transaction.get("bank_name"):
        transaction["bank_name"] = resolved_label
    resolved_account_number = str(account_number or transaction.get("source_account_number") or "").strip()
    if resolved_account_number:
        transaction["source_account_number"] = resolved_account_number

    return transaction


def parse_date(date_str: str) -> date:
    """Parse date string to date object."""
    if not date_str:
        return lagos_today()
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return lagos_today()


def extract_counterparty(narration: str, locale: str = "en") -> str:
    """Extract counterparty name from narration."""
    analysis = analyze_transaction_narration(narration=narration, transaction_type=None)
    if analysis.counterparty:
        return analysis.counterparty
    if analysis.counterparty_role == "bank":
        return render_message("query.fetch.counterparty.bank_charges", locale)
    return render_message("query.fetch.counterparty.unknown", locale)


def _apply_transaction_analysis(transaction: dict[str, Any]) -> dict[str, Any]:
    """Attach narration-derived transaction understanding fields in-place."""
    analysis = analyze_transaction_narration(
        narration=transaction.get("narration", ""),
        transaction_type=transaction.get("type") or transaction.get("transaction_type"),
        provider_category=transaction.get("category"),
        provider_counterparty=transaction.get("counterparty"),
    )
    if not transaction.get("counterparty") and analysis.counterparty is not None:
        transaction["counterparty"] = analysis.counterparty
    if not transaction.get("counterparty_role") and analysis.counterparty_role:
        transaction["counterparty_role"] = analysis.counterparty_role
    if not transaction.get("counterparty_source") and analysis.counterparty_source is not None:
        transaction["counterparty_source"] = analysis.counterparty_source
    if not transaction.get("resolved_category") and analysis.resolved_category is not None:
        transaction["resolved_category"] = analysis.resolved_category
    if not transaction.get("category_source") and analysis.category_source is not None:
        transaction["category_source"] = analysis.category_source
    if not transaction.get("parser_rule") and analysis.parser_rule:
        transaction["parser_rule"] = analysis.parser_rule
    return transaction


def apply_filters(transactions: list[dict[str, Any]], filters: Filters) -> list[dict[str, Any]]:
    """Apply filters to transaction list."""
    result = transactions

    if filters.min_amount is not None:
        result = [t for t in result if abs(t.get("amount", 0)) >= filters.min_amount]

    if filters.max_amount is not None:
        result = [t for t in result if abs(t.get("amount", 0)) <= filters.max_amount]

    if filters.transaction_type:
        result = [t for t in result if t.get("type") == filters.transaction_type]

    if filters.category:
        result = [t for t in result if match_transaction_category(t, filters.category)]

    if filters.counterparty:
        lowered_terms = [term.lower() for term in filters.counterparty if isinstance(term, str) and term.strip()]
        result = [
            t
            for t in result
            if any(
                term in ((t.get("counterparty") or "").lower() or (t.get("narration") or "").lower())
                for term in lowered_terms
            )
        ]

    if filters.merchant:
        result = [t for t in result if any(m.lower() in t.get("narration", "").lower() for m in filters.merchant)]

    if filters.exclude:
        result = [t for t in result if not any(e.lower() in t.get("narration", "").lower() for e in filters.exclude)]

    if filters.account_filter:
        filter_term = filters.account_filter.lower()
        result = [
            t
            for t in result
            if filter_term in (t.get("bank_name", "").lower())
            or filter_term in (t.get("source_account_label", "").lower())
            or filter_term in (str(t.get("source_account_id", "")).lower())
        ]

    return result


def apply_time_window(
    transactions: list[dict[str, Any]],
    *,
    window_start: date | str | None,
    window_end: date | str | None,
) -> list[dict[str, Any]]:
    """Restrict transactions to an inclusive query window."""
    if window_start is None or window_end is None:
        return list(transactions)

    start = window_start if isinstance(window_start, date) else parse_date(window_start)
    end = window_end if isinstance(window_end, date) else parse_date(window_end)

    return [
        transaction
        for transaction in transactions
        if start <= parse_date(str(transaction.get("date", ""))) <= end
    ]


def decide_transaction_cache_reuse(
    *,
    continuation_type: str | None,
    continuation_delta_type: str | None,
    cached_transactions: list[dict[str, Any]] | None,
    cache_age_seconds: float | None,
    max_cache_age_seconds: float,
    cached_fingerprint: str | None,
    current_fingerprint: str | None,
    cached_scope_fingerprint: str | None,
    current_scope_fingerprint: str | None,
    cache_window_start: str | None,
    cache_window_end: str | None,
    current_window_start: str | None,
    current_window_end: str | None,
) -> TransactionCacheReuseDecision:
    """Decide whether a transaction cache snapshot is safe to reuse.

    Exact reuse is for same-envelope filter refinements.
    Time-subset reuse is for narrower windows within the same account/query scope.
    """
    if cached_transactions is None or cache_age_seconds is None or cache_age_seconds > max_cache_age_seconds:
        return TransactionCacheReuseDecision(can_reuse=False, strategy="none")

    exact_reuse = (
        continuation_type == "filter_delta"
        and continuation_delta_type != "time"
        and cached_fingerprint is not None
        and cached_fingerprint == current_fingerprint
    )
    if exact_reuse:
        return TransactionCacheReuseDecision(can_reuse=True, strategy="exact")

    time_subset_reuse = (
        continuation_type == "time_delta"
        and continuation_delta_type == "time"
        and cached_scope_fingerprint is not None
        and cached_scope_fingerprint == current_scope_fingerprint
        and cache_window_start is not None
        and cache_window_end is not None
        and current_window_start is not None
        and current_window_end is not None
        and cache_window_start <= current_window_start
        and current_window_end <= cache_window_end
    )
    if time_subset_reuse:
        return TransactionCacheReuseDecision(can_reuse=True, strategy="time_subset")

    return TransactionCacheReuseDecision(can_reuse=False, strategy="none")


def resolve_query_date_bounds(query_contract: QueryExecutionContract) -> tuple[str, str]:
    """Resolve start/end ISO dates for a query."""
    if query_contract.time_range:
        return query_contract.time_range.start.isoformat(), query_contract.time_range.end.isoformat()

    from datetime import timedelta

    days = 30 if query_contract.intent == "analytics_summary" else 7
    today = lagos_today()
    end = today.isoformat()
    start = (today - timedelta(days=days)).isoformat()
    return start, end


def build_cache_fingerprint(
    query_contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    user_id: str | None = None,
) -> str:
    """Build fingerprint for cache-safe transaction base reuse."""
    start, end = resolve_query_date_bounds(query_contract)
    payload = {
        "intent": str(query_contract.intent),
        "accounts_scope": query_contract.accounts_scope,
        "account_id": account_id,
        "account_ids": sorted(str(acc) for acc in account_ids),
        "start": start,
        "end": end,
        "user_id": str(user_id or ""),
        "transaction_view": "unified" if settings.enable_unified_transaction_view else "bank",
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_cache_scope_fingerprint(
    query_contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    user_id: str | None = None,
) -> str:
    """Build fingerprint for cache reuse across narrower time windows."""
    payload = {
        "intent": str(query_contract.intent),
        "accounts_scope": query_contract.accounts_scope,
        "account_id": account_id,
        "account_ids": sorted(str(acc) for acc in account_ids),
        "user_id": str(user_id or ""),
        "transaction_view": "unified" if settings.enable_unified_transaction_view else "bank",
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _log_query_trace(
    *,
    trace_context: dict[str, Any] | None,
    phase: str,
    latency_ms: float,
    outcome: str,
    fetch_account_count: int,
    used_parallel_fetch: bool,
) -> None:
    context = trace_context or {}
    logger.info(
        "query_trace",
        turn_id=context.get("turn_id"),
        inbound_message_id=context.get("inbound_message_id"),
        query_phase=phase,
        latency_ms=round(latency_ms, 2),
        outcome=outcome,
        fetch_account_count=fetch_account_count,
        used_parallel_fetch=used_parallel_fetch,
    )


async def fetch_and_filter(
    provider: BankDataProvider,
    query_contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    user_id: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> list[dict]:
    """Fetch transactions and apply filters."""
    transactions = await fetch_transactions_base(
        provider,
        query_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        trace_context=trace_context,
    )
    if query_contract.time_range:
        transactions = apply_time_window(
            transactions,
            window_start=query_contract.time_range.start,
            window_end=query_contract.time_range.end,
        )

    if query_contract.filters:
        transactions = apply_filters(transactions, query_contract.filters)

    return transactions


async def fetch_transactions_base(
    provider: BankDataProvider,
    query_contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    user_id: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> list[dict]:
    """Fetch transaction base set for the query time/account envelope (no query.filters applied)."""
    started_at = perf_counter()
    start, end = resolve_query_date_bounds(query_contract)
    start_bound = date.fromisoformat(start)
    end_bound = date.fromisoformat(end)

    bank_map: dict[str, str] = {}
    account_label_map: dict[str, str] = {}
    account_number_map: dict[str, str] = {}
    if accounts_info:
        for acc in accounts_info:
            acc_id = acc.get("account_id") or acc.get("mono_account_id", "")
            label = _account_display_label(acc)
            if acc_id and label:
                bank_map[acc_id] = str(acc.get("bank_name") or label)
                account_label_map[acc_id] = label
            account_number = str(acc.get("account_number") or "").strip()
            if acc_id and account_number:
                account_number_map[acc_id] = account_number

    def to_dict(t: Any) -> dict[str, Any]:
        if hasattr(t, "model_dump"):
            d = cast(dict[str, Any], t.model_dump())
            return _apply_transaction_analysis(d)
        elif is_dataclass(t) and not isinstance(t, type):
            d = asdict(t)
            if "transaction_id" in d:
                d["id"] = d.pop("transaction_id")
            if "transaction_type" in d:
                d["type"] = d.pop("transaction_type")
            return cast(dict[str, Any], _apply_transaction_analysis(d))
        elif isinstance(t, dict):
            d = cast(dict[str, Any], t)
            return _apply_transaction_analysis(d)
        return {"raw": str(t)}

    fetch_account_count = len(account_ids) if query_contract.accounts_scope == "all" and account_ids else 1
    used_parallel_fetch = False
    transactions: list[dict[str, Any]]

    mirrored_accounts = build_mirrored_account_contexts(
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )
    global _MIRROR_SCHEMA_AVAILABLE
    use_mirror = bool(mirrored_accounts) and _MIRROR_SCHEMA_AVAILABLE

    if use_mirror:
        try:
            for mirrored_account in mirrored_accounts:
                await ensure_mirror_coverage(
                    provider,
                    account=mirrored_account,
                    start_date=start_bound,
                    end_date=end_bound,
                )
            transactions = await load_mirrored_transactions(
                account_contexts=mirrored_accounts,
                start_date=start_bound,
                end_date=end_bound,
            )
            for transaction in transactions:
                source_account_id = str(transaction.get("source_account_id") or "").strip()
                source_account_label = str(transaction.get("source_account_label") or "").strip()
                _attach_source_account_metadata(
                    transaction,
                    account_id=source_account_id or None,
                    account_label=source_account_label or transaction.get("bank_name") or None,
                    account_number=account_number_map.get(source_account_id),
                )
        except Exception as e:
            if _is_missing_mirror_table_error(e):
                _MIRROR_SCHEMA_AVAILABLE = False
                logger.info(
                    "bank_transaction_mirror_disabled_missing_schema",
                    fallback="provider_fetch",
                    migration_hint="uv run alembic upgrade head",
                )
            else:
                logger.warning("bank_transaction_mirror_fallback", error=str(e))
            use_mirror = False

    if not use_mirror:
        if query_contract.accounts_scope == "all" and len(account_ids) > 1:
            all_txns: list[dict[str, Any]] = []
            used_parallel_fetch = True
            semaphore = asyncio.Semaphore(min(_MULTI_ACCOUNT_FETCH_CONCURRENCY, len(account_ids)))

            async def _fetch_account_transactions(acc_id: str, slot: int) -> list[dict[str, Any]]:
                async with semaphore:
                    txns = await provider.get_transactions(
                        acc_id,
                        start_date=start,
                        end_date=end,
                        limit=100,
                        user_id=user_id,
                        mock_account_slot=slot,
                    )
                account_transactions: list[dict[str, Any]] = []
                for t in txns:
                    td = to_dict(t)
                    _attach_source_account_metadata(
                        td,
                        account_id=acc_id,
                        account_label=account_label_map.get(acc_id) or bank_map.get(acc_id) or None,
                        account_number=account_number_map.get(acc_id),
                    )
                    account_transactions.append(td)
                return account_transactions

            per_account_transactions = await asyncio.gather(
                *(_fetch_account_transactions(acc_id, slot) for slot, acc_id in enumerate(account_ids))
            )
            for account_transactions in per_account_transactions:
                all_txns.extend(account_transactions)
            transactions = sorted(all_txns, key=lambda t: (t.get("date", ""), t.get("id", "")), reverse=True)
        else:
            slot = account_ids.index(account_id) if account_id in account_ids else 0
            txns = await provider.get_transactions(
                account_id,
                start_date=start,
                end_date=end,
                limit=100,
                user_id=user_id,
                mock_account_slot=slot,
            )
            transactions = [
                _attach_source_account_metadata(
                    to_dict(t),
                    account_id=account_id,
                    account_label=account_label_map.get(account_id) or bank_map.get(account_id) or None,
                    account_number=account_number_map.get(account_id),
                )
                for t in txns
            ]

    transactions = sorted(transactions, key=lambda t: (t.get("date", ""), t.get("id", "")), reverse=True)

    transactions = [t for t in transactions if start <= t.get("date", "")[:10] <= end]

    if settings.enable_unified_transaction_view and user_id:
        try:
            unified_records = await UnifiedTransactionService().list_for_user(
                user_id,
                start_date=start_bound,
                end_date=end_bound,
                bank_transactions=transactions,
            )
            transactions = [record.to_query_dict() for record in unified_records]
        except Exception as exc:
            logger.warning(
                "unified_transaction_fetch_failed",
                error=str(exc),
                user_id=user_id,
                start_date=start,
                end_date=end,
            )

    _log_query_trace(
        trace_context=trace_context,
        phase="fetch_transactions",
        latency_ms=(perf_counter() - started_at) * 1000.0,
        outcome="ok",
        fetch_account_count=fetch_account_count,
        used_parallel_fetch=used_parallel_fetch,
    )
    return transactions
