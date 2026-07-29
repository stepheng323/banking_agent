from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import VarianceDriversEvidenceSelection
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisMetric,
    ConfidencePolicy,
    Dimension,
    MetricSpec,
)
from banking.transactions.query.services.analysis.kernel.metrics import filter_dimension_rows
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
from banking.transactions.query.services.fetching.fetch import parse_date


async def resolve_variance_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    evidence: VarianceDriversEvidenceSelection,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str,
) -> list[dict]:
    analysis = contract.operation.analysis if isinstance(contract.operation, AnalyzeOperation) else None

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
        confidence_policy=(
            ConfidencePolicy(analysis.confidence_policy) if analysis else ConfidencePolicy.SEGMENT_UNCERTAIN
        ),
        completeness_policy=(
            analysis.completeness_policy
            if analysis and analysis.completeness_policy in ("disclose", "require_complete")
            else "require_complete"
        ),
    )

    rows = filter_dimension_rows(
        dataset,
        spec,
        dimension=Dimension(evidence.dimension),
        bucket_key=evidence.bucket_key,
    )

    items = []
    for index, row in enumerate(rows, 1):
        items.append(
            {
                "id": str(row.get("transaction_id") or row.get("id") or index),
                "description": str(
                    row.get("narration")
                    or row.get("counterparty")
                    or render_message("query.common.transaction", language)
                ),
                "amount": abs(float(row.get("amount") or 0)),
                "date": parse_date(str(row.get("date") or "")),
                "metadata": dict(row),
            }
        )
    return items
