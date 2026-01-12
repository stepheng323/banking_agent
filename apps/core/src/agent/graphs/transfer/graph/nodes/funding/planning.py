"""Funding plan creation for multi-account transfers.

Contains the plan_funding function that creates a funding plan
from multiple accounts when the primary account has insufficient balance.
"""

from uuid import UUID

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.formatters.funding import format_insufficient_funds
from shared.services.funding import FundingPlanner
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountAdapter:
    """Adapter to convert dict account data to FundingPlanner interface."""

    def __init__(self, data: dict):
        self.id = UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id")
        self.mono_account_id = data.get("mono_account_id", "")
        self.account_number = data.get("account_number", "")
        self.bank_name = data.get("bank_name", "")
        self.mandate_id = data.get("mandate_id")
        self.mandate_status = data.get("mandate_status", "pending")
        self.is_default = data.get("is_default", False)


async def plan_funding(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
    preferred_account_id: UUID | None = None,
) -> TransferState:
    """
    Create a funding plan using FundingPlanner.

    Uses lazy balance fetching to minimize API calls.
    Supports manual dual-account pooling when user specifies source_accounts.
    """
    amount = state.get("amount", 0)
    accounts = state.get("accounts", [])

    if not accounts:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No linked accounts available.",
            "response": "You don't have any linked accounts for funding.",
        }

    # Filter accounts for manual pooling
    accounts, error_state = _filter_source_accounts(state, accounts)
    if error_state:
        return error_state

    planner = FundingPlanner(direct_debit_provider)
    adapted_accounts = [AccountAdapter(a) for a in accounts]

    plan = await planner.plan_funding(
        accounts=adapted_accounts,
        transfer_amount=amount,
        preferred_account_id=preferred_account_id,
    )

    if not plan.is_sufficient:
        return _handle_insufficient_funds(state, amount, plan)

    plan_dict = _build_plan_dict(plan)

    if plan.is_single_source:
        return {
            **state,
            "funding_plan": plan_dict,
            "funding_steps": plan_dict["steps"],
            "funding_required": False,
            "flow_state": "authorizing",
            "funding_status": "funded",
        }
    else:
        return {
            **state,
            "funding_plan": plan_dict,
            "funding_steps": plan_dict["steps"],
            "flow_state": "confirming_funding",
            "funding_status": "user_confirming",
        }


def _filter_source_accounts(state: TransferState, accounts: list) -> tuple[list, TransferState | None]:
    """Filter accounts based on source_accounts or dual_accounts settings."""
    source_accounts = state.get("source_accounts")
    use_dual_accounts = state.get("use_dual_accounts")

    if source_accounts:
        filtered = [a for a in accounts if a.get("bank_name") in source_accounts]
        if len(filtered) < len(source_accounts):
            missing = [b for b in source_accounts if b not in [a.get("bank_name") for a in filtered]]
            return accounts, {
                **state,
                "flow_state": "error",
                "funding_error": f"Account not found for: {', '.join(missing)}",
                "response": f"You don't have a linked account for {', '.join(missing)}.",
            }
        accounts = filtered[:2]
        logger.info("manual_pooling_filtered", source_accounts=source_accounts, filtered_count=len(accounts))
    elif use_dual_accounts:
        accounts = accounts[:2]
        logger.info("dual_accounts_mode", account_count=len(accounts))

    return accounts, None


def _handle_insufficient_funds(state: TransferState, amount: float, plan) -> TransferState:
    """Build error response for insufficient funds."""
    recipient_name = state.get("recipient_name", "")
    recipient_bank = state.get("recipient_bank_name", "")
    recipient_account = state.get("recipient_account", "")

    account_resolved = state.get("account_resolved")
    if account_resolved and isinstance(account_resolved, dict):
        recipient_name = account_resolved.get("account_name", recipient_name)

    selected_account = state.get("selected_source_account", {})
    primary_bank = selected_account.get("bank_name", "your account")
    primary_balance = plan.steps[0].amount if plan.steps else state.get("balance_available", 0)

    error_msg = format_insufficient_funds(
        transfer_amount=amount,
        bank_name=primary_bank,
        available_balance=primary_balance,
        max_available=plan.total_funded,
        recipient_name=recipient_name,
        recipient_bank=recipient_bank,
        recipient_account=recipient_account,
    )

    return {
        **state,
        "flow_state": "awaiting_amount_adjustment",
        "awaiting_confirmation": True,
        "funding_error": error_msg,
        "response": error_msg,
        "funding_status": "insufficient",
        "max_available": plan.total_funded,
    }


def _build_plan_dict(plan) -> dict:
    """Convert FundingPlan to serializable dict."""
    return {
        "transfer_amount": plan.transfer_amount,
        "total_funded": plan.total_funded,
        "is_sufficient": plan.is_sufficient,
        "num_sources": plan.num_sources,
        "balance_checks": plan.balance_checks,
        "steps": [
            {
                "account_id": str(step.account_id),
                "account_number": step.account_number,
                "bank_name": step.bank_name,
                "mandate_id": step.mandate_id,
                "amount": step.amount,
                "sequence": step.sequence,
            }
            for step in plan.steps
        ],
    }
