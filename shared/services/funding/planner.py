"""Deterministic funding planner for multi-account transfers.

NO LLM INVOLVEMENT - 100% deterministic logic for financial safety.

Strategy: "Default First, Then Largest"
1. Use default account first (up to its balance)
2. If more needed, use other eligible accounts by largest balance
3. Maximum 2 accounts per transfer (MVP limit)
"""
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from shared.clients.abstractions import DirectDebitProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Policy constants
MAX_SOURCE_ACCOUNTS = 2
MIN_FUNDING_AMOUNT = 100.0  # Minimum ₦100 per source


@dataclass
class AccountBalance:
    """Account with current balance for planning."""
    account_id: UUID
    account_number: str
    bank_name: str
    mandate_id: Optional[str]
    mandate_status: str
    is_default: bool
    available_balance: float  # In naira


@dataclass
class FundingStepPlan:
    """Planned debit from a single account."""
    account_id: UUID
    account_number: str
    bank_name: str
    mandate_id: str
    amount: float  # In naira
    sequence: int  # 1, 2, ...


@dataclass
class FundingPlan:
    """Complete funding plan for a transfer."""
    transfer_amount: float
    total_funded: float
    steps: List[FundingStepPlan] = field(default_factory=list)
    is_sufficient: bool = False
    shortfall: float = 0.0
    error: Optional[str] = None
    
    @property
    def num_sources(self) -> int:
        return len(self.steps)
    
    @property
    def is_single_source(self) -> bool:
        return len(self.steps) == 1
    
    @property
    def is_multi_source(self) -> bool:
        return len(self.steps) > 1


class FundingPlanner:
    """
    Deterministic funding planner - NO LLM involvement.
    
    Determines how to fund a transfer from user's linked accounts.
    """
    
    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self._provider = direct_debit_provider
    
    async def plan_funding(
        self,
        accounts: List[AccountBalance],
        transfer_amount: float,
    ) -> FundingPlan:
        """
        Create a funding plan for the requested transfer amount.
        
        Strategy: "Default First, Then Largest"
        1. Filter to eligible accounts (mandate_status == 'ready')
        2. Sort: default first, then by balance descending
        3. Allocate amounts up to MAX_SOURCE_ACCOUNTS
        
        Args:
            accounts: User's linked accounts with balances
            transfer_amount: Amount to fund in naira
            
        Returns:
            FundingPlan with steps or error
        """
        logger.info("planning_funding", 
                   amount=transfer_amount, 
                   account_count=len(accounts))
        
        # Filter eligible accounts (mandate ready)
        eligible = [a for a in accounts if self._is_eligible(a)]
        
        if not eligible:
            return FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                error="No accounts with active mandates. Please set up direct debit first."
            )
        
        # Sort: default first, then by balance descending
        eligible.sort(key=lambda a: (not a.is_default, -a.available_balance))
        
        # Allocate funding
        steps: List[FundingStepPlan] = []
        remaining = transfer_amount
        sequence = 1
        
        for account in eligible[:MAX_SOURCE_ACCOUNTS]:
            if remaining <= 0:
                break
                
            # How much can this account contribute?
            contribution = min(account.available_balance, remaining)
            
            # Skip if contribution is too small
            if contribution < MIN_FUNDING_AMOUNT and remaining >= MIN_FUNDING_AMOUNT:
                continue
            
            if contribution > 0:
                steps.append(FundingStepPlan(
                    account_id=account.account_id,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    mandate_id=account.mandate_id,
                    amount=contribution,
                    sequence=sequence,
                ))
                remaining -= contribution
                sequence += 1
        
        total_funded = transfer_amount - remaining
        is_sufficient = remaining <= 0
        
        plan = FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=total_funded,
            steps=steps,
            is_sufficient=is_sufficient,
            shortfall=max(0, remaining),
        )
        
        if not is_sufficient:
            plan.error = f"Insufficient funds. Need ₦{remaining:,.2f} more."
        
        logger.info("funding_plan_created",
                   is_sufficient=is_sufficient,
                   num_sources=len(steps),
                   total_funded=total_funded)
        
        return plan
    
    def _is_eligible(self, account: AccountBalance) -> bool:
        """Check if account is eligible for debiting."""
        return (
            account.mandate_status == "ready" 
            and account.mandate_id is not None
            and account.available_balance > 0
        )
    
    async def fetch_account_balances(
        self,
        accounts: list,  # List[Account] from database
    ) -> List[AccountBalance]:
        """
        Fetch real-time balances for accounts.
        
        Args:
            accounts: Account model instances from database
            
        Returns:
            List of AccountBalance with current balances
        """
        result = []
        
        for account in accounts:
            try:
                balance_result = await self._provider.get_balance(
                    account.account_id, 
                    real_time=True
                )
                
                result.append(AccountBalance(
                    account_id=account.id,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    mandate_id=account.mandate_id,
                    mandate_status=account.mandate_status,
                    is_default=account.is_default,
                    available_balance=balance_result.available_balance if balance_result.success else 0,
                ))
            except Exception as e:
                logger.error("fetch_balance_failed", 
                           account_id=str(account.id), 
                           error=str(e))
                # Include account with zero balance on error
                result.append(AccountBalance(
                    account_id=account.id,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    mandate_id=account.mandate_id,
                    mandate_status=account.mandate_status,
                    is_default=account.is_default,
                    available_balance=0,
                ))
        
        return result


def format_funding_plan_message(plan: FundingPlan) -> str:
    """Format funding plan for user display."""
    if not plan.is_sufficient:
        return plan.error or "Unable to create funding plan."
    
    if plan.is_single_source:
        step = plan.steps[0]
        return f"₦{plan.transfer_amount:,.2f} will be debited from your {step.bank_name} account."
    
    # Multi-source
    lines = [f"To send ₦{plan.transfer_amount:,.2f}, I'll combine:"]
    for step in plan.steps:
        lines.append(f"• ₦{step.amount:,.2f} from {step.bank_name}")
    lines.append("\nProceed with this plan?")
    
    return "\n".join(lines)
