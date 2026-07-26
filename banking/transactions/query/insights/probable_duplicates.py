"""Insight handler for detecting probable duplicate transactions."""

from __future__ import annotations

from typing import Any

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
from banking.transactions.query.models.extraction import ProbableDuplicatesSpec
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.services.analysis.kernel.contracts import ProbableDuplicatesResult
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
from banking.transactions.query.services.analysis.relationships import detect_probable_duplicates
from banking.transactions.query.models.domain import QueryResultItem
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

    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="current",
    )

    return detect_probable_duplicates(dataset, min_confidence=analysis.min_confidence)


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
            "total_value": format(float(result.total_redundant_value), ".2f"),
        }
    )


def build_probable_duplicates_surface(result: ProbableDuplicatesResult, language: str = "en") -> SurfaceView:
    items = []
    for group in result.candidates[:5]:
        tx_count = len(group.transactions)
        label = render_message("query.insight.duplicates.group_label", language, {"count": tx_count})
        
        evidence = ProbableDuplicatesEvidenceSelection(
            basis=result.basis,
            duplicate_group_id=group.group_id,
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
    for group in result.candidates:
        tx = group.transactions[0]
        items.append({
            "id": group.group_id,
            "description": str(tx.get("narration") or tx.get("counterparty") or render_message("query.common.transaction", language)),
            "amount": abs(float(tx.get("amount") or 0)),
            "date": parse_date(str(tx.get("date") or "")),
            "metadata": {
                "duplicate_count": len(group.transactions),
                "total_redundant_value": float(group.redundant_value),
            }
        })
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
        
    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=evidence.basis,
        period_label="current",
    )
    
    result = detect_probable_duplicates(dataset, min_confidence=0.0)
    
    txs = []
    for group in result.candidates:
        if group.group_id == evidence.duplicate_group_id:
            txs = group.transactions
            break
            
    items: list[dict[str, Any]] = []
    for index, row in enumerate(txs, 1):
        # The registry expects a raw transaction dict, but usually formatted for the surface or just raw rows.
        # We'll just return the row itself since it's already a dict.
        items.append(dict(row))
    return items
