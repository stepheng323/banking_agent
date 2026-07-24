"""Query handler registry."""

from banking.transactions.query.handlers.affordability import handle_affordability
from banking.transactions.query.handlers.analytics import handle_analytics
from banking.transactions.query.handlers.beneficiary import handle_beneficiary_summary
from banking.transactions.query.handlers.cash_flow import handle_cash_flow
from banking.transactions.query.handlers.insight import handle_insight
from banking.transactions.query.handlers.time_comparison import handle_time_comparison
from banking.transactions.query.handlers.transaction_detail import handle_transaction_detail
from banking.transactions.query.handlers.transactions import (
    handle_transaction_list,
)
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    AssessOperation,
    CashFlowSummarySpec,
    CompareOperation,
    GroupedSummarySpec,
    QueryRequest,
    RetrieveOperation,
    SummarizeOperation,
)


def handler_for_request(request: QueryRequest):
    """Resolve execution solely from the authoritative v2 operation."""
    operation = request.operation
    if isinstance(operation, RetrieveOperation):
        if operation.projection.shape in {"fact", "detail"}:
            return handle_transaction_detail
        if operation.projection.shape == "existence":
            return handle_analytics
        return handle_transaction_list
    if isinstance(operation, SummarizeOperation):
        if isinstance(operation.summary, CashFlowSummarySpec):
            return handle_cash_flow
        if isinstance(operation.summary, GroupedSummarySpec) and operation.summary.dimension == "counterparty":
            return handle_beneficiary_summary
        return handle_analytics
    if isinstance(operation, CompareOperation):
        return handle_time_comparison
    if isinstance(operation, AnalyzeOperation):
        return handle_insight
    if isinstance(operation, AssessOperation):
        return handle_affordability
    raise ValueError(f"unsupported query operation: {operation.kind}")
