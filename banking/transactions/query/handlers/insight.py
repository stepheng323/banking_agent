"""Deterministic insight handlers built over the shared analysis kernel."""

from __future__ import annotations

from datetime import date
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
        if not isinstance(insight_result, definition.result_type):
            raise TypeError("insight executor returned an unsupported result type")
        presentation = definition.presenter(insight_result, language)
    except Exception as exc:
        logger.error(
            "insight_processing_failed",
            error_type=type(exc).__name__,
            insight_type=analysis.insight_type,
        )
        return QueryResult(summary_text=render_message("query.error.execution_failed", language))

    result_metadata = getattr(insight_result, "metadata", None)
    effective_start = getattr(result_metadata, "effective_start", None)
    effective_end = getattr(result_metadata, "effective_end", None)
    if isinstance(effective_start, date) and isinstance(effective_end, date):
        effective_days = (effective_end - effective_start).days + 1
    else:
        effective_days = None
    candidates = (
        getattr(insight_result, "candidates", None)
        or getattr(insight_result, "series", None)
        or getattr(insight_result, "anomalies", None)
        or getattr(insight_result, "groups", None)
        or []
    )
    confidences = [float(value) for item in candidates if (value := getattr(item, "confidence", None)) is not None]
    confidence_band = (
        "high" if confidences and max(confidences) >= 0.9 else "medium" if confidences else "not_applicable"
    )
    logger.info(
        "query_insight_completed",
        insight_type=analysis.insight_type,
        basis=analysis.analysis_basis,
        effective_days=effective_days,
        coverage=getattr(getattr(result_metadata, "coverage", None), "value", "unavailable"),
        included_count=getattr(result_metadata, "included_count", 0),
        excluded_count=getattr(result_metadata, "excluded_count", 0),
        uncertain_count=getattr(result_metadata, "uncertain_count", 0),
        candidate_count=len(candidates),
        confidence_band=confidence_band,
        available=getattr(result_metadata, "available", getattr(insight_result, "available", True)),
    )
    summary_text = presentation.summary_text
    is_available = bool(getattr(result_metadata, "available", getattr(insight_result, "available", True)))
    if getattr(getattr(result_metadata, "coverage", None), "value", None) == "partial" and getattr(
        result_metadata, "available", False
    ):
        summary_text = f"{summary_text}\n\n{render_message('query.insight.coverage_partial', language)}"
    surface_view = presentation.surface_view.model_copy(
        update={"lead_text": summary_text, "items": presentation.surface_view.items if is_available else []}
    )
    interpretation: dict[str, Any] = {
        "insight_type": analysis.insight_type,
        "analysis_basis": analysis.analysis_basis,
        "confidence_policy": analysis.confidence_policy,
        "completeness_policy": analysis.completeness_policy,
        "uncertain_value": float(
            getattr(result_metadata, "uncertain_value", getattr(insight_result, "uncertainty", 0.0))
        ),
        "excluded_value": float(
            getattr(result_metadata, "excluded_value", getattr(insight_result, "excluded_value", 0.0))
        ),
        "coverage": getattr(
            getattr(result_metadata, "coverage", getattr(insight_result, "coverage", None)),
            "value",
            "unavailable",
        ),
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
        items=presentation.items if is_available and presentation.items else None,
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
        logger.error(
            "insight_evidence_failed",
            error_type=type(exc).__name__,
            insight_type=analysis.insight_type,
        )
        return QueryResult(summary_text=render_message("query.error.execution_failed", language))
    logger.info(
        "query_insight_evidence_resolved",
        insight_type=analysis.insight_type,
        basis=evidence.basis,
        resolved_count=len(resolved_items),
        outcome="found" if resolved_items else "empty",
    )

    return QueryResult(
        summary_text=render_message(
            "query.insight.evidence_summary",
            language,
            {"count": len(resolved_items)},
        ),
        items=[QueryResultItem(**item) for item in resolved_items] if resolved_items else None,
        has_more=False,
        answer_strategy=QueryAnswerStrategy.TRANSACTION_LIST,
        interpretation={
            "insight_type": analysis.insight_type,
            "evidence_basis": evidence.basis,
        },
    )
