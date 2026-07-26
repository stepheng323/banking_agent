"""Deterministic insight handlers built over the shared analysis kernel."""

from __future__ import annotations

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.insights.registry import insight_registry
from banking.transactions.query.models.domain import (
    QueryAnswerStrategy,
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
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

    definition = insight_registry.get(analysis.insight_type)
    if not definition:
        logger.warning("unregistered_insight_type", insight_type=analysis.insight_type)
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
        insight_result = await definition.executor(
            service=service,
            contract=contract,
            account_id=account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
            language=language,
        )
    except Exception as exc:
        logger.error("insight_execution_failed", error=str(exc), insight_type=analysis.insight_type)
        return QueryResult(summary_text=render_message("query.error.execution_failed", language))

    if not isinstance(insight_result, definition.result_type):
        logger.error("insight_result_type_unsupported", insight_type=analysis.insight_type)
        return QueryResult(summary_text=render_message("query.insight.unavailable", language))

    summary_text = definition.formatter(insight_result, language)
    surface_view = definition.surface_builder(insight_result, language)
    item_metadata_list = definition.item_builder(insight_result, language)

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
        "insight_type": analysis.insight_type,
        "analysis_basis": analysis.analysis_basis,
        "confidence_policy": analysis.confidence_policy,
        "completeness_policy": analysis.completeness_policy,
        "uncertain_value": float(getattr(insight_result, "uncertain_value", getattr(insight_result, "uncertainty", 0.0))),
        "excluded_value": float(getattr(insight_result, "excluded_value", 0.0)),
        "coverage": getattr(insight_result, "coverage", None).value if hasattr(insight_result, "coverage") else 1.0,
    }

    # Optional extensions for variance
    if analysis.insight_type == "variance_drivers" and hasattr(insight_result, "metric_comparisons"):
        for comparison in insight_result.metric_comparisons:
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
        answer_strategy=QueryAnswerStrategy.INSIGHT,
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

    definition = insight_registry.get(analysis.insight_type)
    if not definition or not definition.evidence_resolver:
        return QueryResult(summary_text=render_message("query.insight.unavailable", language))

    try:
        resolved_items = await definition.evidence_resolver(
            service=service,
            contract=contract,
            evidence=evidence,
            account_id=account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
            language=language,
        )
    except Exception as exc:
        logger.error("insight_evidence_failed", error=str(exc), insight_type=analysis.insight_type)
        return QueryResult(summary_text=render_message("query.error.execution_failed", language))

    return QueryResult(
        summary_text=render_message(
            "query.insight.evidence_summary",
            language,
            {"count": len(resolved_items)},
        ),
        items=resolved_items,
        has_more=False,
        answer_strategy=QueryAnswerStrategy.TRANSACTION_LIST,
        interpretation={
            "insight_type": analysis.insight_type,
            "evidence_basis": evidence.basis,
        },
    )
