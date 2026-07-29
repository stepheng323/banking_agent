"""Insight handler for detecting probable duplicate transactions."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    InsightEvidenceSelection,
    ProbableDuplicatesEvidenceSelection,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, ProbableDuplicatesSpec
from banking.transactions.query.services.analysis.kernel.contracts import ProbableDuplicatesResult
from banking.transactions.query.services.analysis.kernel.insight_support import evidence_item, inclusive_window
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
from banking.transactions.query.services.analysis.relationships import detect_probable_duplicates
from banking.transactions.query.services.fetching.fetch import parse_date


async def execute_probable_duplicates(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> ProbableDuplicatesResult:
    """Execute probable duplicates detection."""
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation):
        raise ValueError("insight execution requires an AnalyzeOperation")
    analysis = operation.analysis
    if not isinstance(analysis, ProbableDuplicatesSpec):
        raise ValueError("expected ProbableDuplicatesSpec")

    if analysis.lookback_days > 0:
        period = operation.scope.period
        window_start, window_end = inclusive_window(end=period.end, days=analysis.lookback_days)
        expanded_period = period.model_copy(update={"start": window_start, "end": window_end})
        contract = contract.model_copy(
            deep=True,
            update={
                "operation": operation.model_copy(
                    update={"scope": operation.scope.model_copy(update={"period": expanded_period})}
                )
            },
        )

    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="current",
    )

    return detect_probable_duplicates(
        dataset,
        min_confidence=analysis.min_confidence,
        completeness_policy=analysis.completeness_policy,
    )


def format_probable_duplicates(result: ProbableDuplicatesResult, language: str = "en") -> str:
    if not result.available:
        return render_message("query.insight.coverage_required", language)
    if not result.candidates:
        return render_message("query.insight.duplicates.none", language)
    return render_message(
        "query.insight.duplicates.found",
        language,
        {
            "count": len(result.candidates),
            "total_value": format_naira_compact(result.total_redundant_value),
        },
    )


def build_probable_duplicates_surface(result: ProbableDuplicatesResult, language: str = "en") -> SurfaceView:
    items = []
    for group in result.candidates[:5]:
        tx_count = len(group.transactions)
        label = render_message("query.insight.duplicates.group_label", language, {"count": tx_count})

        evidence = ProbableDuplicatesEvidenceSelection(
            basis=result.basis,
            duplicate_group_id=group.group_id,
            effective_start=result.dataset.start_date.isoformat(),
            effective_end=result.dataset.end_date.isoformat(),
            transaction_ids=[
                str(row.get("transaction_id") or row.get("id"))
                for row in group.transactions
                if row.get("transaction_id") or row.get("id")
            ][:20],
        )

        payload = SelectionPayload(
            selection_kind="summary_scope",
            label=label,
            insight_evidence=evidence,
        )

        items.append(
            SurfaceItemView(
                id=group.group_id,
                label=label,
                amount=float(group.redundant_value),
                count=tx_count,
                payload=payload,
            )
        )
    return SurfaceView(mode=SurfaceViewMode.INSIGHT, items=items)


def build_probable_duplicates_items(result: ProbableDuplicatesResult, language: str = "en") -> list[dict[str, Any]]:
    items = []
    for group in result.candidates[:5]:
        tx = group.transactions[0]
        items.append(
            {
                "id": group.group_id,
                "description": str(
                    tx.get("narration")
                    or tx.get("counterparty")
                    or render_message("query.common.transaction", language)
                ),
                "amount": abs(float(tx.get("amount") or 0)),
                "date": parse_date(str(tx.get("date") or "")),
                "metadata": {
                    "duplicate_count": len(group.transactions),
                    "total_redundant_value": float(group.redundant_value),
                },
            }
        )
    return items


async def resolve_probable_duplicates_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    evidence: InsightEvidenceSelection,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> list[dict[str, Any]]:
    """Resolve specific duplicate transactions for an evidence drill-down."""
    if not isinstance(evidence, ProbableDuplicatesEvidenceSelection):
        raise ValueError("Expected ProbableDuplicatesEvidenceSelection")

    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation):
        raise ValueError("Evidence selection requires an AnalyzeOperation")

    if evidence.effective_start and evidence.effective_end:
        from banking.transactions.query.services.fetching.fetch import parse_date

        start = parse_date(evidence.effective_start)
        end = parse_date(evidence.effective_end)
        if start and end:
            period = operation.scope.period
            contract = contract.model_copy(
                deep=True,
                update={
                    "operation": operation.model_copy(
                        update={
                            "scope": operation.scope.model_copy(
                                update={"period": period.model_copy(update={"start": start, "end": end})}
                            )
                        }
                    )
                },
            )

    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=evidence.basis,
        period_label="current",
    )

    result = detect_probable_duplicates(
        dataset,
        min_confidence=0.0,
        completeness_policy="disclose",
    )

    txs = []
    for group in result.candidates:
        if group.group_id == evidence.duplicate_group_id:
            txs = group.transactions
            break

    items: list[dict[str, Any]] = []
    allowed_ids = set(evidence.transaction_ids)
    for index, row in enumerate(txs, 1):
        row_id = str(row.get("transaction_id") or row.get("id") or "")
        if allowed_ids and row_id not in allowed_ids:
            continue
        items.append(evidence_item(row, fallback_id=f"duplicate-{index}"))
    return items
