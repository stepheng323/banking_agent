"""Executors for native analysis operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from banking.transactions.query.insights.variance import execute_variance_drivers
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.services.analysis.kernel.contracts import VarianceAnalysisResult
from banking.transactions.query.services.analysis.kernel.service import AnalysisService

InsightExecutor = Callable[..., Awaitable[BaseModel]]


async def execute_variance(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> VarianceAnalysisResult:
    """Dispatch variance-drivers execution through the shared kernel."""
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation):
        raise ValueError("variance execution requires an AnalyzeOperation")
    analysis = operation.analysis

    basis = analysis.analysis_basis
    current_dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=basis,
        period_label="current",
    )

    current_range = operation.scope.period

    from banking.transactions.query.handlers.time_comparison import _get_comparison_period

    baseline_range = _get_comparison_period(current_range, baseline=analysis.baseline)
    baseline_contract = contract.model_copy(
        update={
            "operation": operation.model_copy(
                update={"scope": operation.scope.model_copy(update={"period": baseline_range})}
            )
        },
        deep=True,
    )

    baseline_dataset = await service.load_dataset(
        baseline_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=basis,
        period_label="baseline",
    )

    return await execute_variance_drivers(
        current_dataset=current_dataset,
        baseline_dataset=baseline_dataset,
        measure=analysis.measure,
        dimensions=list(analysis.dimensions),
        basis=basis,
        confidence_policy=analysis.confidence_policy,
        completeness_policy=analysis.completeness_policy,
        evidence_limit=analysis.evidence_limit,
        language=language,
    )


INSIGHT_EXECUTORS: dict[str, InsightExecutor] = {
    "variance_drivers": execute_variance,
}


async def execute_insight(
    insight_type: str,
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> BaseModel:
    """Dispatch an insight executor by type."""
    executor = INSIGHT_EXECUTORS.get(insight_type)
    if executor is None:
        raise NotImplementedError(f"insight type not implemented: {insight_type}")
    return await executor(
        service=service,
        contract=contract,
        account_id=account_id,
        account_ids=account_ids,
        accounts_info=accounts_info,
        user_id=user_id,
        language=language,
    )
