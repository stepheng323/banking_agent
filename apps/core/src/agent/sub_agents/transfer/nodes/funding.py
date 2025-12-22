"""Funding nodes for multi-account transfer workflow.

These nodes handle the multi-account funding flow:
1. check_funding - Check if default account has sufficient balance
2. plan_funding - Create funding plan from multiple accounts
3. confirm_funding - Ask user to confirm multi-account plan
4. initiate_debits - Start direct debits from source accounts
5. check_debit_status - Poll/check if debits completed
"""
from typing import Any, Optional
from uuid import UUID

from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.services.funding import FundingPlanner, FundingPlan, format_funding_plan_message
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def check_funding(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Check if the selected source account has sufficient balance.
    
    If sufficient: proceed to authorize (normal flow)
    If insufficient: set funding_required=True for multi-account flow
    """
    amount = state.get("amount", 0)
    selected_account = state.get("selected_source_account")
    
    if not selected_account:
        return {
            **state,
            "flow_state": "error",
            "response": "No source account selected.",
            "funding_error": "No source account selected",
        }
    
    account_id = selected_account.get("mono_account_id")
    if not account_id:
        # No mono account - can't check balance, assume sufficient
        logger.warning("no_mono_account_id", account=selected_account.get("id"))
        return {
            **state,
            "funding_required": False,
            "flow_state": "authorizing",
        }
    
    try:
        balance_result = await direct_debit_provider.get_balance(account_id, real_time=True)
        balance = balance_result.available_balance if balance_result.success else 0
        
        logger.info("balance_checked",
                    account_id=account_id,
                    balance=balance,
                    required=amount)
        
        if balance >= amount:
            # Sufficient - proceed normally
            return {
                **state,
                "balance_available": balance,
                "funding_required": False,
                "flow_state": "authorizing",
            }
        else:
            # Insufficient - need multi-account funding
            return {
                **state,
                "balance_available": balance,
                "funding_required": True,
                "flow_state": "planning_funding",
                "funding_status": "pending",
            }
    except Exception as e:
        logger.error("balance_check_failed", error=str(e))
        # On error, assume sufficient and let normal flow handle it
        return {
            **state,
            "funding_required": False,
            "flow_state": "authorizing",
        }


async def plan_funding(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
    preferred_account_id: Optional[UUID] = None,
) -> TransferState:
    """
    Create a funding plan using FundingPlanner.
    
    Uses lazy balance fetching to minimize API calls.
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
    
    planner = FundingPlanner(direct_debit_provider)
    
    class AccountAdapter:
        def __init__(self, data: dict):
            self.id = UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id")
            self.mono_account_id = data.get("mono_account_id", "")
            self.account_number = data.get("account_number", "")
            self.bank_name = data.get("bank_name", "")
            self.mandate_id = data.get("mandate_id")
            self.mandate_status = data.get("mandate_status", "pending")
            self.is_default = data.get("is_default", False)
    
    adapted_accounts = [AccountAdapter(a) for a in accounts]
    
    plan = await planner.plan_funding(
        accounts=adapted_accounts,
        transfer_amount=amount,
        preferred_account_id=preferred_account_id,
    )
    
    if not plan.is_sufficient:
        return {
            **state,
            "flow_state": "error",
            "funding_error": plan.error,
            "response": plan.error or "Unable to create funding plan.",
            "funding_status": "failed",
        }
    
    plan_dict = {
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
        ]
    }
    
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
        message = format_funding_plan_message(plan)
        return {
            **state,
            "funding_plan": plan_dict,
            "funding_steps": plan_dict["steps"],
            "flow_state": "confirming_funding",
            "funding_status": "user_confirming",
            "response": message,
        }


async def confirm_funding(
    state: TransferState,
    whatsapp_client: WhatsAppClient,
) -> TransferState:
    """
    Send funding plan confirmation to user and wait for response.
    """
    phone_number = state.get("phone_number", "")
    funding_plan = state.get("funding_plan", {})
    
    if not funding_plan:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding plan available.",
        }
    
    steps = funding_plan.get("steps", [])
    amount = funding_plan.get("transfer_amount", 0)
    
    lines = [f"To send ₦{amount:,.2f}, I'll combine:"]
    for step in steps:
        lines.append(f"• ₦{step['amount']:,.2f} from {step['bank_name']}")
    lines.append("\nReply 'yes' to proceed or 'no' to cancel.")
    
    message = "\n".join(lines)
    
    await whatsapp_client.send_text_message(phone_number, message)
    
    return {
        **state,
        "flow_state": "confirming_funding",
        "response": message,
    }


async def initiate_debits(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Initiate direct debits for each funding step.
    """
    funding_steps = state.get("funding_steps", [])
    idempotency_key = state.get("idempotency_key", "")
    
    if not funding_steps:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding steps to execute.",
        }
    
    initiated_steps = []
    
    for step in funding_steps:
        reference = f"{idempotency_key}_{step['sequence']}"
        
        try:
            result = await direct_debit_provider.initiate_debit(
                mandate_id=step["mandate_id"],
                amount=step["amount"],
                reference=reference,
                narration=f"Transfer funding step {step['sequence']}",
            )
            
            initiated_steps.append({
                **step,
                "debit_id": result.debit_id,
                "reference": reference,
                "status": result.status.value if result.success else "failed",
                "error": result.error_message,
            })
            
            logger.info("debit_initiated",
                       step=step["sequence"],
                       debit_id=result.debit_id,
                       success=result.success)
                       
        except Exception as e:
            logger.error("debit_initiation_failed",
                        step=step["sequence"],
                        error=str(e))
            initiated_steps.append({
                **step,
                "status": "failed",
                "error": str(e),
            })
    
    failed = [s for s in initiated_steps if s.get("status") == "failed"]
    
    if failed:
        return {
            **state,
            "funding_steps": initiated_steps,
            "flow_state": "error",
            "funding_status": "failed",
            "funding_error": f"Failed to initiate {len(failed)} debit(s).",
        }
    
    return {
        **state,
        "funding_steps": initiated_steps,
        "flow_state": "awaiting_debits",
        "funding_status": "debiting",
    }
