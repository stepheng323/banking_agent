"""Aggregate limit checking for workflows.

Validates total amounts across tasks before execution.
"""

from decimal import Decimal
from typing import Any

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.models import get_task_risk, TaskRisk

logger = get_logger(__name__)


# Default limits (can be overridden per-user)
DEFAULT_LIMITS = {
    "daily_transfer_limit": Decimal("5_000_000"),      # ₦5M daily
    "per_transaction_limit": Decimal("1_000_000"),     # ₦1M per tx
    "bulk_transfer_limit": Decimal("2_000_000"),       # ₦2M per bulk
    "daily_airtime_limit": Decimal("100_000"),         # ₦100k daily
    "daily_data_limit": Decimal("100_000"),            # ₦100k daily
}


class LimitViolation:
    """Represents a limit violation."""
    
    def __init__(self, limit_type: str, limit_value: Decimal, actual_value: Decimal, message: str):
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.actual_value = actual_value
        self.message = message
    
    def to_user_message(self) -> str:
        return self.message


class AggregateResult:
    """Result from aggregate limit check."""
    
    def __init__(self, allowed: bool, violations: list[LimitViolation] | None = None):
        self.allowed = allowed
        self.violations = violations or []
    
    @classmethod
    def ok(cls) -> "AggregateResult":
        return cls(allowed=True)
    
    @classmethod
    def denied(cls, violations: list[LimitViolation]) -> "AggregateResult":
        return cls(allowed=False, violations=violations)
    
    def to_user_message(self) -> str | None:
        if self.allowed:
            return None
        return "\n".join(v.to_user_message() for v in self.violations)


def check_aggregate_limits(
    tasks: list[PlannedTask],
    user_limits: dict[str, Decimal] | None = None,
    daily_spent: dict[str, Decimal] | None = None,
) -> AggregateResult:
    """
    Check aggregate limits across all tasks.
    
    Args:
        tasks: List of planned tasks
        user_limits: Optional per-user limits override
        daily_spent: Already spent amounts today {executor: amount}
    
    Returns:
        AggregateResult with allowed=True or violations list
    """
    limits = {**DEFAULT_LIMITS, **(user_limits or {})}
    spent = daily_spent or {}
    violations = []
    
    # Group amounts by executor type
    amounts_by_executor: dict[str, Decimal] = {}
    
    for task in tasks:
        if get_task_risk(task.executor) != TaskRisk.MONEY_MOVE:
            continue
        
        amount = _extract_amount(task.parameters)
        if amount is None:
            continue
        
        executor = task.executor
        amounts_by_executor[executor] = amounts_by_executor.get(executor, Decimal(0)) + amount
        
        # Per-transaction limit
        if executor == "transfer" and amount > limits["per_transaction_limit"]:
            violations.append(LimitViolation(
                limit_type="per_transaction",
                limit_value=limits["per_transaction_limit"],
                actual_value=amount,
                message=f"Single transfer of ₦{amount:,.0f} exceeds limit of ₦{limits['per_transaction_limit']:,.0f}.",
            ))
    
    # Check bulk transfer limit
    total_transfer = amounts_by_executor.get("transfer", Decimal(0))
    if total_transfer > limits["bulk_transfer_limit"]:
        violations.append(LimitViolation(
            limit_type="bulk_transfer",
            limit_value=limits["bulk_transfer_limit"],
            actual_value=total_transfer,
            message=f"Total transfer of ₦{total_transfer:,.0f} exceeds bulk limit of ₦{limits['bulk_transfer_limit']:,.0f}.",
        ))
    
    # Check daily limits
    for executor, amount in amounts_by_executor.items():
        already_spent = spent.get(executor, Decimal(0))
        total_today = already_spent + amount
        
        if executor == "transfer":
            if total_today > limits["daily_transfer_limit"]:
                violations.append(LimitViolation(
                    limit_type="daily_transfer",
                    limit_value=limits["daily_transfer_limit"],
                    actual_value=total_today,
                    message=f"Today's transfers would total ₦{total_today:,.0f}, exceeding daily limit of ₦{limits['daily_transfer_limit']:,.0f}.",
                ))
        elif executor == "airtime":
            if total_today > limits["daily_airtime_limit"]:
                violations.append(LimitViolation(
                    limit_type="daily_airtime",
                    limit_value=limits["daily_airtime_limit"],
                    actual_value=total_today,
                    message=f"Today's airtime purchases would total ₦{total_today:,.0f}, exceeding daily limit.",
                ))
        elif executor == "data":
            if total_today > limits["daily_data_limit"]:
                violations.append(LimitViolation(
                    limit_type="daily_data",
                    limit_value=limits["daily_data_limit"],
                    actual_value=total_today,
                    message=f"Today's data purchases would total ₦{total_today:,.0f}, exceeding daily limit.",
                ))
    
    if violations:
        return AggregateResult.denied(violations)
    
    return AggregateResult.ok()


from shared.types.planner import TaskParameters

def _extract_amount(params: TaskParameters | dict[str, Any]) -> Decimal | None:
    """Extract amount from task parameters."""
    if isinstance(params, dict):
        amount = params.get("amount") or params.get("budget")
    else:
        amount = params.amount or params.budget

    if amount is None:
        return None
    
    try:
        return Decimal(str(amount))
    except (ValueError, TypeError):
        return None
