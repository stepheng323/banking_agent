"""Batch authorization utilities and constants."""

from typing import List
from shared.types.planner import PlannedTask

# Executors that require PIN authorization
AUTH_REQUIRED_EXECUTORS = {"transfer", "airtime", "data"}

# Execution states
class ExecutionState:
    """Execution state constants."""
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    READY_FOR_AUTH = "READY_FOR_AUTH"
    AUTHORIZING = "AUTHORIZING"
    EXECUTING_BATCH = "EXECUTING_BATCH"
    COMPLETE = "COMPLETE"


def requires_authorization(task: PlannedTask) -> bool:
    """
    Check if a task requires PIN authorization.
    
    Args:
        task: Planned task to check
        
    Returns:
        True if task requires authorization, False otherwise
    """
    return task.executor in AUTH_REQUIRED_EXECUTORS


def filter_auth_required_tasks(tasks: List[PlannedTask]) -> List[PlannedTask]:
    """
    Filter tasks that require authorization.
    
    Args:
        tasks: List of planned tasks
        
    Returns:
        List of tasks requiring authorization
    """
    return [task for task in tasks if requires_authorization(task)]


def mask_account_number(account_number: str) -> str:
    """
    Mask account number for display.
    
    Args:
        account_number: Full account number
        
    Returns:
        Masked account number (e.g., "8162...023")
    """
    if not account_number or len(account_number) < 6:
        return account_number
    
    return f"{account_number[:4]}...{account_number[-3:]}"


def format_amount(amount: float) -> str:
    """
    Format amount with currency symbol.
    
    Args:
        amount: Amount to format
        
    Returns:
        Formatted amount (e.g., "₦5,000")
    """
    return f"₦{amount:,.0f}"
