"""Analysis data-source interface and implementations.

Consumers of the kernel ask for an ``AnalysisDataset``; they do not know
whether it came from a bank provider, the local ledger mirror, or economic
events.  This keeps handlers testable and removes provider-specific storage
logic from conversational code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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
        coverage_status, missing_accounts = await _resolve_ledger_coverage(
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
            coverage_status=coverage_status,
            missing_accounts=missing_accounts,
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

        coverage_status, missing_accounts = await _ensure_and_resolve_ledger_coverage(
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
            coverage_status=coverage_status,
            missing_accounts=missing_accounts,
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
) -> tuple[CoverageStatus, list[str]]:
    contexts = build_mirrored_account_contexts(
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )
    if not contexts:
        return CoverageStatus.UNAVAILABLE, list(account_ids)
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
            return CoverageStatus.PARTIAL, [context.external_account_id for context in contexts]
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
) -> tuple[CoverageStatus, list[str]]:
    contexts = build_mirrored_account_contexts(
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
    )
    if not contexts:
        return CoverageStatus.UNAVAILABLE, list(account_ids)

    missing: list[str] = []
    try:
        async with UnitOfWork() as uow:
            if uow.bank_transaction_coverages is None:
                return CoverageStatus.UNAVAILABLE, list(account_ids)
            for context in contexts:
                covered = await uow.bank_transaction_coverages.is_window_covered(
                    context.linked_account_id,
                    start_date=contract.time_start,
                    end_date=contract.time_end,
                    provider="mono",
                )
                if not covered:
                    missing.append(context.external_account_id)
    except Exception:
        return CoverageStatus.UNAVAILABLE, list(account_ids)

    if not missing:
        return CoverageStatus.COMPLETE, []
    if len(missing) == len(contexts):
        return CoverageStatus.UNAVAILABLE, missing
    return CoverageStatus.PARTIAL, missing
