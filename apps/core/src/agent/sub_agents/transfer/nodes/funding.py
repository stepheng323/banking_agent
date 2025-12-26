"""Funding nodes for multi-account transfer workflow.

These nodes handle the multi-account funding flow:
1. check_funding - Check if default account has sufficient balance
2. plan_funding - Create funding plan from multiple accounts
3. confirm_funding - Ask user to confirm multi-account plan
4. initiate_debits - Start direct debits from source accounts
5. check_debit_status - Poll/check if debits completed
"""
from typing import Any, Optional
import uuid
from uuid import UUID

from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.cache.redis_client import RedisClient
from shared.services.funding import FundingPlanner
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.utils.logging import get_logger
from shared.formatters.transfer import format_funding_plan_summary
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from datetime import datetime, timedelta


logger = get_logger(__name__)


async def check_funding(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
    whatsapp_client: WhatsAppClient,
    actionable_message_repo: Optional[ActionableMessageRepository] = None,
) -> TransferState:
    """
    Check if the selected source account has sufficient balance.
    
    If sufficient: send PIN confirmation flow and proceed to authorize
    If insufficient: set funding_required=True for multi-account flow
    """
    amount = state.get("amount", 0)
    selected_account = state.get("selected_source_account")
    
    logger.info("DEBUG_TRACE_CHECK_FUNDING_ENTRY", amount=amount, selected_account=selected_account)
    
    if not selected_account:
        return {
            **state,
            "flow_state": "error",
            "response": "No source account selected.",
            "funding_error": "No source account selected",
        }
    
    account_id = selected_account.get("account_id")
    if not account_id:
        logger.warning("no_account_id_for_balance", account=selected_account.get("id"))
        return {
            **state,
            "flow_state": "error",
            "response": "This account is not properly linked. Please unlink and re-add it.",
            "funding_error": "No account_id for balance check",
        }
    
    try:
        balance_result = await direct_debit_provider.get_balance(account_id, real_time=True)
        balance = balance_result.available_balance if balance_result.success else 0
        
        logger.info("balance_checked",
                    account_id=account_id,
                    balance=balance,
                    required=amount)
        
        if balance >= amount:
            token = state.get("confirmation_token", "")
            summary = state.get("confirmation_summary", "")
            
            logger.info("check_funding_token_summary_check",
                       has_token=bool(token),
                       has_summary=bool(summary),
                       token_preview=token[:20] if token else "NONE",
                       summary_preview=summary[:50] if summary else "NONE")
            
            if token and summary:
                flow_result = await whatsapp_client.send_flow(
                    to=state["phone_number"],
                    header="Confirm Your Transfer",
                    flow_cta="Authorize Transfer",
                    flow_id=settings.pin_confirmation_flow_id,
                    screen_name="Pin",
                    flow_token=token,
                    text_body=summary,
                )
                
                # Store confirmation message for quote-based repeats
                if actionable_message_repo:
                    wa_message_id = flow_result.get("messages", [{}])[0].get("id", "")
                    user_id = state.get("user_profile", {}).get("id")
                    if wa_message_id and user_id:
                        account_resolved = state.get("account_resolved", {})
                        actionable_message_repo.create(
                            user_id=user_id,
                            wa_message_id=wa_message_id,
                            message_type="transfer_confirmation",
                            message_data={
                                "amount": state.get("amount"),
                                "recipient_name": account_resolved.get("account_name"),
                                "recipient_account": account_resolved.get("account_number"),
                                "recipient_bank_code": state.get("recipient_bank_code"),
                                "recipient_bank_name": state.get("recipient_bank_name"),
                            },
                            expires_at=datetime.utcnow() + timedelta(days=90),
                        )
            
            return {
                **state,
                "balance_available": balance,
                "funding_required": False,
                "flow_state": "authorizing",
            }
        else:
            return {
                **state,
                "balance_available": balance,
                "funding_required": True,
                "flow_state": "planning_funding",
                "funding_status": "pending",
            }
    except Exception as e:
        logger.error("balance_check_failed", error=str(e))
        return {
            **state,
            "flow_state": "error",
            "response": "Unable to verify your balance. Please try again later.",
            "funding_error": f"Balance check failed: {str(e)}",
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
        # Get recipient details for error message
        recipient_name = state.get("recipient_name", "")
        recipient_bank = state.get("recipient_bank_name", "")
        recipient_account = state.get("recipient_account", "")
        
        # Use resolved name if available
        account_resolved = state.get("account_resolved")
        if account_resolved and isinstance(account_resolved, dict):
            recipient_name = account_resolved.get("account_name", recipient_name)
        
        # Get selected account info for the error message
        selected_account = state.get("selected_source_account", {})
        primary_bank = selected_account.get("bank_name", "your account")
        primary_balance = plan.steps[0].amount if plan.steps else state.get("balance_available", 0)
        
        # Build error message with recipient details
        from shared.formatters.funding import format_insufficient_funds
        error_msg = format_insufficient_funds(
            transfer_amount=amount,
            bank_name=primary_bank,
            available_balance=primary_balance,
            max_available=plan.total_funded,
            recipient_name=recipient_name,
            recipient_bank=recipient_bank,
            recipient_account=recipient_account,
        )
        
        # Stay in flow so user can adjust amount - don't end the transfer
        return {
            **state,
            "flow_state": "awaiting_amount_adjustment",  # Allow user to send new amount
            "awaiting_confirmation": True,  # Keep session active so orchestrator routes here
            "funding_error": error_msg,
            "response": error_msg,
            "funding_status": "insufficient",
            "max_available": plan.total_funded,  # Store for reference
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
        # Multi-source: needs user confirmation - confirm_funding will send the message
        return {
            **state,
            "funding_plan": plan_dict,
            "funding_steps": plan_dict["steps"],
            "flow_state": "confirming_funding",
            "funding_status": "user_confirming",
            # Note: Don't set 'response' - confirm_funding sends the detailed message
        }


async def confirm_funding(
    state: TransferState,
    whatsapp_client: WhatsAppClient,
    redis_client: RedisClient,
) -> TransferState:
    """
    Send funding plan confirmation via Secure PIN Flow.
    This allows user to confirm AND authorize in one step.
    """
    phone_number = state.get("phone_number", "")
    funding_plan = state.get("funding_plan", {})
    balance_available = state.get("balance_available", 0)
    amount = state.get("amount", 0) or 0
    
    if not funding_plan:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding plan available.",
        }
    
    steps = funding_plan.get("steps", [])
    selected_account = state.get("selected_source_account", {})
    primary_bank = selected_account.get("bank_name", "your account")
    
    # Get recipient details from state
    recipient_name = state.get("recipient_name", "")
    recipient_bank = state.get("recipient_bank_name", "")
    recipient_account = state.get("recipient_account", "")
    
    # If we have account_resolved, use the resolved name
    account_resolved = state.get("account_resolved")
    if account_resolved and isinstance(account_resolved, dict):
        recipient_name = account_resolved.get("account_name", recipient_name)
    
    summary = format_funding_plan_summary(
        steps=steps,
        amount=amount,
        primary_bank=primary_bank,
        balance_available=balance_available,
        recipient_name=recipient_name,
        recipient_bank=recipient_bank,
        recipient_account=recipient_account,
    )
    
    token = uuid.uuid4().hex
    flow_token = f"transfer-pin-{token}"
    
    # Store mapping for webhook to find phone number
    # Key format must match handle_transaction_pin lookup: transfer:token:{token}:phone
    await redis_client.set(f"transfer:token:{token}:phone", phone_number, ex=3600)
    
    # Send Flow
    await whatsapp_client.send_flow(
        to=phone_number,
        header="Confirm Funding",
        flow_cta="Authorize Funding",
        flow_id=settings.pin_confirmation_flow_id,
        screen_name="Pin",
        flow_token=flow_token,
        text_body=summary,
    )
    
    return {
        **state,
        "awaiting_confirmation": True,
        "confirmation_token": token, # Store internal token
        "confirmation_context": {
            "flow_type": "transfer",
            "action": "funding_approval",
            "callback_data": {
                "funding_plan": state.get("funding_plan"),
                "amount": amount,
            },
            "clarification_prompt": (
                "To proceed with this funding plan, please click 'Authorize Funding' above and enter your PIN.\n"
                "Or reply 'NO' to cancel."
            ),
        },
        "flow_state": "confirming_funding",
        "funding_required": True,
        "_amount_at_confirmation": amount,
        "response": "",  # Clear stale response - WhatsApp flow was sent directly
        "llm_reply": None,
    }


async def verify_funding_approval(
    state: TransferState,
    authorization_service: Any,
) -> TransferState:
    """
    Verify if funding was approved via PIN flow or text.
    Runs after extract node when flow_state is confirming_funding.
    """
    phone_number = state.get("phone_number", "")
    
    logger.info("verify_funding_approval_ENTRY", 
               phone=phone_number,
               pin_verified_in_state=state.get("pin_verified"),
               funding_approved=state.get("funding_approved"),
               flow_state=state.get("flow_state"),
               confirmation_token=state.get("confirmation_token", "")[:20] if state.get("confirmation_token") else None)
    
    # Check verification result using token from this funding flow
    # AuthorizationService stores result keyed by the inner token (token from transfer-pin-{token})
    token = state.get("confirmation_token")
    idempotency_key = token if token else state.get("idempotency_key", "")
    
    # Check if PIN was verified (via Flow event or Redis)
    is_pin_verified_in_state = state.get("pin_verified")
    pin_result = await authorization_service.get_pin_verification_result(idempotency_key)
    
    if is_pin_verified_in_state or (pin_result and pin_result.verified):
        logger.info("funding_pin_verified_via_flow", phone=phone_number, from_state=is_pin_verified_in_state)
        return {
            **state,
            "funding_approved": True,
            "pin_verified": True,
            "awaiting_confirmation": False,
        }
    
    logger.info("funding_verification_failed", 
                phone=phone_number, 
                used_key=idempotency_key,
                has_result=bool(pin_result),
                verified=pin_result.verified if pin_result else None)
    
    # Check if text approval (from AffirmationHandler logic)
    if state.get("funding_approved"):
        logger.info("funding_approved_via_text_but_pin_missing", phone=phone_number)
        return {
            **state,
            "awaiting_confirmation": True, # Still waiting
            "funding_approved": False, # Reset to prevent loop
            "response": "Please tap 'Authorize Funding' in the message above to confirm securely with your PIN.",
        }
    
    # If explicit rejection, it's handled by cancellation logic usually, 
    # but if valid unverified response comes through:
    return state


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
                "provider": direct_debit_provider.provider_name,  # Add provider name for database records
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


async def wait_for_debits(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Check status of all initiated debits.
    
    Returns:
        - If all successful: flow_state=initiating_payout
        - If any failed: flow_state=error, triggers refund
        - If still pending: flow_state=awaiting_debits (retry later)
    """
    funding_steps = state.get("funding_steps", [])
    
    if not funding_steps:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding steps to check.",
        }
    
    updated_steps = []
    all_successful = True
    any_failed = False
    any_pending = False
    
    for step in funding_steps:
        debit_id = step.get("debit_id")
        current_status = step.get("status", "pending")
        
        if current_status == "successful":
            updated_steps.append(step)
            continue
        
        if current_status == "failed":
            updated_steps.append(step)
            any_failed = True
            all_successful = False
            continue
        
        if not debit_id:
            updated_steps.append({**step, "status": "failed", "error": "No debit_id"})
            any_failed = True
            all_successful = False
            continue
        
        try:
            result = await direct_debit_provider.get_debit_status(debit_id)
            new_status = result.status.value if result.success else "failed"
            
            updated_steps.append({
                **step,
                "status": new_status,
                "error": result.error_message if not result.success else None,
            })
            
            if new_status == "successful":
                logger.info("debit_confirmed", debit_id=debit_id)
            elif new_status == "failed":
                any_failed = True
                all_successful = False
                logger.error("debit_failed", debit_id=debit_id, error=result.error_message)
            else:
                any_pending = True
                all_successful = False
                logger.info("debit_still_pending", debit_id=debit_id, status=new_status)
                
        except Exception as e:
            logger.error("debit_status_check_failed", debit_id=debit_id, error=str(e))
            updated_steps.append({**step, "status": "pending"})
            any_pending = True
            all_successful = False
    
    if any_failed:
        return {
            **state,
            "funding_steps": updated_steps,
            "flow_state": "error",
            "funding_status": "failed",
            "funding_error": "One or more debits failed. Initiating refund.",
        }
    
    if all_successful:
        logger.info("wait_for_debits_ALL_SUCCESSFUL", flow_state="initiating_payout", funding_status="funded")
        return {
            **state,
            "funding_steps": updated_steps,
            "flow_state": "initiating_payout",
            "funding_status": "funded",
            "response": None,
            "llm_reply": None,
        }
    
    import asyncio
    if any_pending:
        # Prevent tight looping and API hammering
        await asyncio.sleep(3)

    return {
        **state,
        "funding_steps": updated_steps,
        "flow_state": "awaiting_debits",
        "funding_status": "debiting",
    }

