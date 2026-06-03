"""Read-only coverage and sync explanations for query sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from banking.accounts.mandate_state import READY, effective_mandate_status
from banking.transactions.query.models.domain import QueryExecutionContract
from banking.transactions.query.services.fetching.fetch import _is_missing_mirror_table_error
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AccountCoverageSnapshot:
    bank_name: str
    suffix: str
    mandate_status: str
    covered: bool | None
    last_synced_at: str | None
    reason: str

    @property
    def label(self) -> str:
        return f"{self.bank_name}{self.suffix}" if self.suffix else self.bank_name


def _normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _account_id(account: dict[str, Any]) -> str:
    return str(account.get("account_id") or account.get("mono_account_id") or account.get("id") or "").strip()


def _linked_account_id(account: dict[str, Any]) -> str:
    return str(account.get("id") or "").strip()


def _bank_name(account: dict[str, Any]) -> str:
    return str(account.get("bank_name") or account.get("institution_name") or _account_id(account) or "Account").strip()


def _suffix(account: dict[str, Any]) -> str:
    account_number = str(account.get("account_number") or "").strip()
    if len(account_number) >= 4:
        return f" (···{account_number[-4:]})"
    return ""


def _mandate_status(account: dict[str, Any]) -> str:
    return effective_mandate_status(account)


def _sync_metadata(account: dict[str, Any]) -> dict[str, Any]:
    extra_data = account.get("extra_data")
    if isinstance(extra_data, dict):
        raw_sync = extra_data.get("bank_transaction_sync")
        if isinstance(raw_sync, dict):
            return raw_sync
    return {}


def _account_provider(account: dict[str, Any]) -> str:
    return (
        str(account.get("provider_name") or account.get("provider") or settings.account_provider_name).strip().lower()
    )


def _format_date(value: date) -> str:
    return value.strftime("%b %d, %Y")


def _format_sync_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%b %d, %Y %H:%M")


def _resolve_window(
    *,
    query_contract: QueryExecutionContract | None,
    session: dict[str, Any],
) -> tuple[date | None, date | None]:
    cache_start = session.get("cache_window_start")
    cache_end = session.get("cache_window_end")
    if isinstance(cache_start, str) and isinstance(cache_end, str):
        try:
            return date.fromisoformat(cache_start[:10]), date.fromisoformat(cache_end[:10])
        except ValueError:
            pass
    if query_contract is not None:
        return query_contract.time_start, query_contract.time_end
    return None, None


def _matching_accounts(accounts: list[dict[str, Any]], target_text: str | None) -> list[dict[str, Any]]:
    target = _normalize(target_text)
    if not target:
        return list(accounts)
    return [
        account
        for account in accounts
        if target in _normalize(_bank_name(account))
        or target in _normalize(str(account.get("account_name") or ""))
        or target in _normalize(str(account.get("institution_name") or ""))
        or target in _normalize(str(account.get("account_number") or ""))
    ]


async def _coverage_for_account(
    account: dict[str, Any],
    *,
    start_date: date | None,
    end_date: date | None,
) -> AccountCoverageSnapshot:
    bank_name = _bank_name(account)
    suffix = _suffix(account)
    mandate_status = _mandate_status(account)
    sync_metadata = _sync_metadata(account)
    last_synced_at = _format_sync_time(str(sync_metadata.get("last_recent_sync_at") or "").strip() or None)

    if mandate_status and mandate_status != READY:
        return AccountCoverageSnapshot(
            bank_name=bank_name,
            suffix=suffix,
            mandate_status=mandate_status,
            covered=False,
            last_synced_at=last_synced_at,
            reason="authorization_pending",
        )

    linked_account_id = _linked_account_id(account)
    if not linked_account_id or start_date is None or end_date is None:
        return AccountCoverageSnapshot(
            bank_name=bank_name,
            suffix=suffix,
            mandate_status=mandate_status,
            covered=None,
            last_synced_at=last_synced_at,
            reason="coverage_metadata_unavailable",
        )

    try:
        from banking.persistence.unit_of_work import UnitOfWork

        async with UnitOfWork() as uow:
            if uow.bank_transaction_coverages is None:
                raise RuntimeError("bank_transaction_coverage_repository_unavailable")
            covered = await uow.bank_transaction_coverages.is_window_covered(
                linked_account_id,
                start_date=start_date,
                end_date=end_date,
                provider=_account_provider(account),
            )
    except Exception as exc:
        reason = "coverage_schema_unavailable" if _is_missing_mirror_table_error(exc) else "coverage_check_unavailable"
        logger.info(
            "query_coverage_check_unavailable",
            reason=reason,
            bank_name=bank_name,
            error=str(exc),
        )
        return AccountCoverageSnapshot(
            bank_name=bank_name,
            suffix=suffix,
            mandate_status=mandate_status,
            covered=None,
            last_synced_at=last_synced_at,
            reason=reason,
        )

    return AccountCoverageSnapshot(
        bank_name=bank_name,
        suffix=suffix,
        mandate_status=mandate_status,
        covered=covered,
        last_synced_at=last_synced_at,
        reason="covered" if covered else "coverage_gap",
    )


def _status_line(snapshot: AccountCoverageSnapshot) -> str:
    if snapshot.covered is True:
        sync_text = f" Last sync: {snapshot.last_synced_at}." if snapshot.last_synced_at else ""
        return f"✓ {snapshot.label}: coverage confirmed.{sync_text}"
    if snapshot.reason == "authorization_pending":
        return f"○ {snapshot.label}: authorization is {snapshot.mandate_status}, so transaction sync is not complete."
    if snapshot.covered is False:
        return f"○ {snapshot.label}: coverage is incomplete for this period."
    return f"? {snapshot.label}: coverage could not be confirmed right now."


def _single_account_answer(snapshot: AccountCoverageSnapshot, *, start_date: date | None, end_date: date | None) -> str:
    period = f" for {_format_date(start_date)}–{_format_date(end_date)}" if start_date and end_date else ""
    if snapshot.reason == "authorization_pending":
        return (
            f"{snapshot.label} is not fully synced because account authorization is {snapshot.mandate_status}.\n\n"
            "Complete the account authorization first, then I can include its transactions in query results."
        )
    if snapshot.covered is True:
        sync_text = f"\nLast sync: {snapshot.last_synced_at}" if snapshot.last_synced_at else ""
        return f"{snapshot.label} has confirmed transaction coverage{period}.{sync_text}"
    if snapshot.covered is False:
        return (
            f"{snapshot.label} has a transaction coverage gap{period}.\n\n"
            "I can show the transactions I have locally, but I cannot claim the list is complete for that account."
        )
    return (
        f"I can show the transactions I have locally for {snapshot.label}, "
        "but I cannot confirm full coverage right now."
    )


async def build_query_coverage_answer(
    *,
    accounts_info: list[dict[str, Any]],
    query_contract: QueryExecutionContract | None,
    session: dict[str, Any],
    target_text: str | None,
) -> str:
    """Build a conservative, read-only coverage answer from account/session state."""
    accounts = [account for account in accounts_info if _account_id(account)]
    if not accounts:
        return "I can show local transaction results, but I do not have linked-account metadata to confirm coverage."

    selected_accounts = _matching_accounts(accounts, target_text)
    if target_text and not selected_accounts:
        return f"I don't see {target_text.strip()} among your linked accounts."
    if target_text and len(selected_accounts) > 1:
        labels = ", ".join(_bank_name(account) for account in selected_accounts[:3])
        return f"I found multiple linked accounts matching {target_text.strip()}: {labels}. Which one do you mean?"

    start_date, end_date = _resolve_window(query_contract=query_contract, session=session)
    snapshots = [
        await _coverage_for_account(account, start_date=start_date, end_date=end_date) for account in selected_accounts
    ]

    if target_text and len(snapshots) == 1:
        return _single_account_answer(snapshots[0], start_date=start_date, end_date=end_date)

    period = f" for {_format_date(start_date)}–{_format_date(end_date)}" if start_date and end_date else ""
    confirmed_count = sum(1 for snapshot in snapshots if snapshot.covered is True)
    unknown_count = sum(1 for snapshot in snapshots if snapshot.covered is None)
    incomplete_count = len(snapshots) - confirmed_count - unknown_count

    if confirmed_count == len(snapshots):
        header = f"Yes. I have confirmed transaction coverage{period} for your linked accounts."
    else:
        header = (
            f"I can show the transactions I have locally{period}, "
            "but I cannot confirm the full set for every linked account."
        )

    lines = [header, ""]
    lines.extend(_status_line(snapshot) for snapshot in snapshots)
    if incomplete_count or unknown_count:
        lines.extend(
            [
                "",
                "So treat the current result as the best local view, not a guaranteed complete statement.",
            ]
        )
    return "\n".join(lines)
