"""Analysis data-source interface and implementations.

Consumers of the kernel ask for an ``AnalysisDataset``; they do not know
whether it came from a bank provider, the local ledger mirror, or economic
events.  This keeps handlers testable and removes provider-specific storage
logic from conversational code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.services.analysis.economic_events import load_economic_event_rows
from banking.transactions.query.services.analysis.kernel.contracts import AnalysisBasis, AnalysisDataset, CoverageStatus
from banking.transactions.query.services.fetching.bank_transaction_mirror import (
    build_mirrored_account_contexts,
    ensure_mirror_coverage,
)
from banking.transactions.query.services.fetching.fetch import apply_filters, fetch_and_filter
from shared.clients.abstractions.banking import BankDataProvider


class AnalysisDataSource(ABC):
    """Load a normalized transaction-like dataset for a query contract."""

    basis: AnalysisBasis

    @abstractmethod
    async def load(
        self,
        contract: QueryRequest,
        account_id: str,
        account_ids: list[str],
        accounts_info: list[dict] | None,
        *,
        user_id: str | None,
        period_label: str,
    ) -> AnalysisDataset:
        """Return rows and coverage metadata for the requested period."""


@dataclass(frozen=True, slots=True)
class _CoverageResolution:
    status: CoverageStatus
    missing_accounts: list[str]
    missing_gaps: dict[str, list[tuple[date, date]]]
    covered_account_count: int
    requested_account_count: int

    def dataset_fields(self, *, start: date, end: date) -> dict[str, object]:
        requested_days = max((end - start).days + 1, 0)
        # A day is fully covered only when every selected account has coverage.
        # Merge all account gaps to obtain the uncovered union for the scope.
        ranges = [gap for gaps in self.missing_gaps.values() for gap in gaps]
        uncovered = _merged_days(ranges)
        fully_covered_days = max(requested_days - uncovered, 0) if self.covered_account_count else 0
        return {
            "requested_days": requested_days,
            "fully_covered_days": fully_covered_days,
            "covered_account_count": self.covered_account_count,
            "requested_account_count": self.requested_account_count,
            "missing_account_gaps": self.missing_gaps,
        }


def _merged_days(ranges: list[tuple[date, date]]) -> int:
    if not ranges:
        return 0
    merged: list[tuple[date, date]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
            continue
        prior_start, prior_end = merged[-1]
        merged[-1] = (prior_start, max(prior_end, end))
    return sum((end - start).days + 1 for start, end in merged)


class LedgerTransactionSource(AnalysisDataSource):
    """Bank-provider / ledger mirror source."""

    basis: AnalysisBasis = "ledger_transactions"

    def __init__(self, provider: BankDataProvider):
        self.provider = provider

    async def load(
        self,
        contract: QueryRequest,
        account_id: str,
        account_ids: list[str],
        accounts_info: list[dict] | None,
        *,
        user_id: str | None,
        period_label: str,
    ) -> AnalysisDataset:
        rows = await fetch_and_filter(
            self.provider,
            contract,
            account_id,
            account_ids,
            accounts_info,
            user_id=user_id,
        )
        coverage = await _resolve_ledger_coverage(
            contract=contract,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
        )
        return AnalysisDataset(
            basis=self.basis,
            period_label=period_label,
            start_date=contract.time_start,
            end_date=contract.time_end,
            rows=rows,
            coverage_status=coverage.status,
            missing_accounts=coverage.missing_accounts,
            **coverage.dataset_fields(start=contract.time_start, end=contract.time_end),  # type: ignore[arg-type]
            unresolved_count=sum(
                1 for r in rows if r.get("semantic_resolution_state") in {"partial", "needs_review", "unknown"}
            ),
            unresolved_value=sum(
                (
                    Decimal(str(r.get("amount") or 0)).copy_abs()
                    for r in rows
                    if r.get("semantic_resolution_state") in {"partial", "needs_review", "unknown"}
                ),
                Decimal("0"),
            ),
        )


class EconomicEventSource(AnalysisDataSource):
    """Local economic-event projection source."""

    basis: AnalysisBasis = "economic_events"

    def __init__(self, provider: BankDataProvider | None = None):
        self.provider = provider

    async def load(
        self,
        contract: QueryRequest,
        account_id: str,
        account_ids: list[str],
        accounts_info: list[dict] | None,
        *,
        user_id: str | None,
        period_label: str,
    ) -> AnalysisDataset:
        if user_id is None:
            return AnalysisDataset(
                basis=self.basis,
                period_label=period_label,
                start_date=contract.time_start,
                end_date=contract.time_end,
                rows=[],
                coverage_status=CoverageStatus.UNAVAILABLE,
                missing_accounts=account_ids,
            )

        coverage = await _ensure_and_resolve_ledger_coverage(
            provider=self.provider,
            contract=contract,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
        )

        scope = set(account_ids)
        linked_account_ids = [
            account.get("id")
            for account in (accounts_info or [])
            if str(account.get("account_id") or account.get("mono_account_id") or "") in scope and account.get("id")
        ]
        if not linked_account_ids:
            return AnalysisDataset(
                basis=self.basis,
                period_label=period_label,
                start_date=contract.time_start,
                end_date=contract.time_end,
                rows=[],
                coverage_status=CoverageStatus.UNAVAILABLE,
                missing_accounts=account_ids,
            )

        async with UnitOfWork() as uow:
            rows = await load_economic_event_rows(
                uow.db,
                user_id=user_id,
                start_date=contract.time_start,
                end_date=contract.time_end,
                linked_account_ids=linked_account_ids,
            )
        if contract.filters:
            rows = apply_filters(rows, contract.filters)

        return AnalysisDataset(
            basis=self.basis,
            period_label=period_label,
            start_date=contract.time_start,
            end_date=contract.time_end,
            rows=rows,
            coverage_status=coverage.status,
            missing_accounts=coverage.missing_accounts,
            **coverage.dataset_fields(start=contract.time_start, end=contract.time_end),  # type: ignore[arg-type]
            unresolved_count=sum(
                1 for r in rows if r.get("semantic_resolution_state") in {"partial", "needs_review", "unknown"}
            ),
            unresolved_value=sum(
                (
                    Decimal(str(r.get("amount") or 0)).copy_abs()
                    for r in rows
                    if r.get("semantic_resolution_state") in {"partial", "needs_review", "unknown"}
                ),
                Decimal("0"),
            ),
        )


def build_analysis_source(
    basis: str,
    *,
    provider: BankDataProvider | None = None,
) -> AnalysisDataSource:
    """Factory for analysis data sources."""
    if basis == "economic_events":
        return EconomicEventSource(provider)
    if basis == "ledger_transactions":
        if provider is None:
            raise ValueError("ledger_transactions source requires a BankDataProvider")
        return LedgerTransactionSource(provider)
    raise ValueError(f"unsupported analysis basis: {basis}")


async def _ensure_and_resolve_ledger_coverage(
    *,
    provider: BankDataProvider | None,
    contract: QueryRequest,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    user_id: str | None,
) -> _CoverageResolution:
    contexts = build_mirrored_account_contexts(
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )
    if not contexts:
        return _CoverageResolution(CoverageStatus.UNAVAILABLE, list(account_ids), {}, 0, len(account_ids))
    if provider is not None:
        try:
            for context in contexts:
                await ensure_mirror_coverage(
                    provider,
                    account=context,
                    start_date=contract.time_start,
                    end_date=contract.time_end,
                )
        except Exception:
            # The analysis can still use the durable rows already present, but it
            # must not claim complete coverage after a failed coverage refresh.
            gaps = {
                context.external_account_id: [(contract.time_start, contract.time_end)]
                for context in contexts
            }
            return _CoverageResolution(CoverageStatus.PARTIAL, list(gaps), gaps, 0, len(contexts))
    return await _resolve_ledger_coverage(
        contract=contract,
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )


async def _resolve_ledger_coverage(
    *,
    contract: QueryRequest,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    user_id: str | None,
) -> _CoverageResolution:
    contexts = build_mirrored_account_contexts(
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )
    if not contexts:
        return _CoverageResolution(CoverageStatus.UNAVAILABLE, list(account_ids), {}, 0, len(account_ids))

    missing: list[str] = []
    missing_gaps: dict[str, list[tuple[date, date]]] = {}
    try:
        async with UnitOfWork() as uow:
            if uow.bank_transaction_coverages is None:
                    return _CoverageResolution(CoverageStatus.UNAVAILABLE, list(account_ids), {}, 0, len(contexts))
            for context in contexts:
                gaps = await uow.bank_transaction_coverages.find_missing_gaps(
                    context.linked_account_id,
                    start_date=contract.time_start,
                    end_date=contract.time_end,
                    provider="mono",
                )
                if gaps:
                    missing.append(context.external_account_id)
                    missing_gaps[context.external_account_id] = gaps
    except Exception:
        return _CoverageResolution(CoverageStatus.UNAVAILABLE, list(account_ids), {}, 0, len(contexts))

    if not missing:
        return _CoverageResolution(CoverageStatus.COMPLETE, [], {}, len(contexts), len(contexts))
    if len(missing) == len(contexts):
        return _CoverageResolution(CoverageStatus.UNAVAILABLE, missing, missing_gaps, 0, len(contexts))
    return _CoverageResolution(
        CoverageStatus.PARTIAL,
        missing,
        missing_gaps,
        len(contexts) - len(missing),
        len(contexts),
    )
