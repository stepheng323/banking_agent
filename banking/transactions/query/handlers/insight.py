"""Deterministic insight handlers built over the shared analysis kernel."""

from __future__ import annotations

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.insights import INSIGHT_EXECUTORS, execute_insight
from banking.transactions.query.models.domain import (
    QueryAnswerStrategy,
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.presentation.insights.variance import (
    build_variance_query_items,
    build_variance_surface_view,
    format_variance_result,
)
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisMetric,
    ConfidencePolicy,
    Dimension,
    MetricSpec,
    VarianceAnalysisResult,
)
from banking.transactions.query.services.analysis.kernel.metrics import filter_dimension_rows
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
from banking.transactions.query.services.fetching.fetch import parse_date
from shared.clients.abstractions.banking import BankDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_insight(
    provider: BankDataProvider,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
) -> QueryResult:
    """Dispatch a registered insight deterministically; no extra LLM call."""
    del current_page, page_size
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation):
        return QueryResult(summary_text=render_message("query.insight.missing_directive", language))
    analysis = operation.analysis

    if analysis.type not in INSIGHT_EXECUTORS:
        return QueryResult(summary_text=render_message("query.insight.unavailable", language))

    service = AnalysisService(provider)
    if analysis.evidence is not None:
        return await _handle_insight_evidence(
            service,
            contract,
            account_id,
            account_ids,
            accounts_info,
            user_id=user_id,
            language=language,
        )

    try:
        insight_result = await execute_insight(
            insight_type=analysis.type,
            service=service,
            contract=contract,
            account_id=account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
            language=language,
        )
    except Exception as exc:
        logger.error("insight_execution_failed", error=str(exc), insight_type=analysis.type)
        return QueryResult(summary_text=render_message("query.error.execution_failed", language))

    if not isinstance(insight_result, VarianceAnalysisResult):
        logger.error("insight_result_type_unsupported", insight_type=analysis.type)
        return QueryResult(summary_text=render_message("query.insight.unavailable", language))
    variance_result = insight_result

    summary_text = format_variance_result(variance_result, language=language)
    surface_view = build_variance_surface_view(variance_result, language=language)

    item_metadata_list = build_variance_query_items(variance_result, language=language)
    items: list[QueryResultItem] = []
    for item, surface_item in zip(item_metadata_list, surface_view.items, strict=False):
        metadata = dict(item["metadata"])
        metadata["selection_payload"] = surface_item.payload.model_dump()
        items.append(
            QueryResultItem(
                id=item["id"],
                description=item["description"],
                amount=item["amount"],
                date=item["date"],
                metadata=metadata,
            )
        )

    interpretation: dict[str, Any] = {
        "insight_type": analysis.type,
        "measure": analysis.measure,
        "analysis_basis": analysis.analysis_basis,
        "dimensions": list(analysis.dimensions),
        "confidence_policy": analysis.confidence_policy,
        "completeness_policy": analysis.completeness_policy,
        "uncertain_value": float(variance_result.uncertainty),
        "excluded_value": float(variance_result.excluded_value),
        "residual": float(variance_result.residual),
        "coverage": variance_result.coverage.value,
    }
    for comparison in variance_result.metric_comparisons:
        interpretation[comparison.metric.value] = {
            "current_value": float(comparison.current_value),
            "baseline_value": float(comparison.baseline_value),
            "absolute_delta": float(comparison.absolute_delta),
            "relative_delta": float(comparison.relative_delta) if comparison.relative_delta is not None else None,
            "relative_status": comparison.relative_status,
        }

    return QueryResult(
        summary_text=summary_text,
        items=items,
        surface_view=surface_view,
        answer_strategy=QueryAnswerStrategy.VARIANCE_INSIGHT,
        interpretation=interpretation,
    )


async def _handle_insight_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str,
) -> QueryResult:
    operation = contract.operation
    analysis = operation.analysis if isinstance(operation, AnalyzeOperation) else None
    evidence = analysis.evidence if analysis is not None else None
    if analysis is None or evidence is None:
        return QueryResult(summary_text=render_message("query.insight.missing_directive", language))

    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=evidence.basis,
        period_label="current",
    )
    spec = MetricSpec(
        metric=AnalysisMetric(evidence.metric),
        basis=evidence.basis,
        confidence_policy=ConfidencePolicy(analysis.confidence_policy),
        completeness_policy=analysis.completeness_policy,
    )
    rows = filter_dimension_rows(
        dataset,
        spec,
        dimension=Dimension(evidence.dimension),
        bucket_key=evidence.bucket_key,
    )
    items = [
        QueryResultItem(
            id=str(row.get("transaction_id") or row.get("id") or index),
            description=str(
                row.get("narration") or row.get("counterparty") or render_message("query.common.transaction", language)
            ),
            amount=abs(float(row.get("amount") or 0)),
            date=parse_date(str(row.get("date") or "")),
            metadata=dict(row),
        )
        for index, row in enumerate(rows, 1)
    ]
    return QueryResult(
        summary_text=render_message(
            "query.insight.evidence_summary",
            language,
            {"count": len(items)},
        ),
        items=items,
        has_more=False,
        answer_strategy=QueryAnswerStrategy.TRANSACTION_LIST,
        interpretation={
            "insight_type": analysis.type,
            "evidence_dimension": evidence.dimension,
            "evidence_metric": evidence.metric,
            "coverage": dataset.coverage_status.value,
        },
    )
