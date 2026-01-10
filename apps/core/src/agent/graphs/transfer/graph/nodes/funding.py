"""Funding nodes for multi-account transfer workflow.

These nodes handle the multi-account funding flow:
1. check_funding - Check if default account has sufficient balance
2. plan_funding - Create funding plan from multiple accounts
3. confirm_funding - Ask user to confirm multi-account plan
4. initiate_debits - Start direct debits from source accounts
5. check_debit_status - Poll/check if debits completed
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import RedisClient
from shared.clients.abstractions import DirectDebitProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.formatters.transfer import format_funding_plan_summary
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.funding import FundingPlanner
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def check_funding(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
    whatsapp_client: WhatsAppClient,
    actionable_message_repo: ActionableMessageRepository | None = None,
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

        logger.info("balance_checked", account_id=account_id, balance=balance, required=amount)

        # Handle transfer_all: set amount to available balance
        transfer_all = state.get("transfer_all")
        if transfer_all and not amount:
            amount = balance
            logger.info("transfer_all_amount_set", balance=balance, amount=amount)

            # Regenerate confirmation summary with the actual amount
            from shared.formatters.transfer import format_transfer_summary

            account_resolved = state.get("account_resolved", {})
            rec_name = (
                account_resolved.get("account_name")
                if account_resolved and isinstance(account_resolved, dict)
                else state.get("recipient_name") or "Recipient"
            )
            bank_name = state.get("recipient_bank_name") or state.get("recipient_bank_code") or ""
            acct_number = state.get("recipient_account")
            source = state.get("selected_source_account", {})
            source_account_number = source.get("account_number") or ""
            source_bank_name = source.get("bank_name") or source.get("name") or "Account"
            narration = state.get("narration")

            new_summary = format_transfer_summary(
                {
                    "amount": amount,
                    "recipientName": rec_name,
                    "recipientBank": bank_name,
                    "recipientAccount": acct_number,
                    "sourceBank": source_bank_name,
                    "sourceAccount": source_account_number,
                    "narration": narration,
                }
            )

            # Update state with the calculated amount and new summary
            state = {**state, "amount": amount, "confirmation_summary": new_summary}

        # Handle transfer_percentage: calculate amount from percentage
        transfer_percentage = state.get("transfer_percentage")
        if transfer_percentage:
            # Always calculate from percentage, even if amount was somehow set
            amount = balance * (transfer_percentage / 100)
            logger.info(
                "transfer_percentage_calculated",
                percentage=transfer_percentage,
                balance=balance,
                calculated_amount=amount,
            )

            # Regenerate confirmation summary with calculated amount
            from shared.formatters.transfer import format_transfer_summary

            account_resolved = state.get("account_resolved", {})
            rec_name = (
                account_resolved.get("account_name")
                if account_resolved and isinstance(account_resolved, dict)
                else state.get("recipient_name") or "Recipient"
            )
            bank_name = state.get("recipient_bank_name") or state.get("recipient_bank_code") or ""
            acct_number = state.get("recipient_account")
            source = state.get("selected_source_account", {})
            source_account_number = source.get("account_number") or ""
            source_bank_name = source.get("bank_name") or source.get("name") or "Account"
            narration = state.get("narration")

            new_summary = format_transfer_summary(
                {
                    "amount": amount,
                    "recipientName": rec_name,
                    "recipientBank": bank_name,
                    "recipientAccount": acct_number,
                    "sourceBank": source_bank_name,
                    "sourceAccount": source_account_number,
                    "narration": narration,
                    "percentage": transfer_percentage,  # Include percentage for display
                }
            )

            # Update state with the calculated amount and new summary
            state = {**state, "amount": amount, "confirmation_summary": new_summary}

        if balance >= (amount or 0):
            token = state.get("confirmation_token", "")
            summary = state.get("confirmation_summary", "")

            logger.info(
                "check_funding_token_summary_check",
                has_token=bool(token),
                has_summary=bool(summary),
                token_preview=token[:20] if token else "NONE",
                summary_preview=summary[:50] if summary else "NONE",
            )

            if token and summary:
                if state.get("message_id"):
                    await whatsapp_client.send_typing_indicator(state["message_id"])
                    await asyncio.sleep(0.3)  # Allow WhatsApp to render typing indicator

                flow_result = await whatsapp_client.send_flow(
                    to=state["phone_number"],
                    header="Confirm Your Transfer",
                    flow_cta="Authorize Transfer",
                    flow_id=settings.pin_confirmation_flow_id,
                    screen_name="Pin",
                    flow_token=token,
                    text_body=summary,
                )

                wa_message_id = flow_result.get("messages", [{}])[0].get("id", "")
                user_id = state.get("user_profile", {}).get("id")
                logger.info(
                    "actionable_message_check",
                    has_wa_message_id=bool(wa_message_id),
                    has_user_id=bool(user_id),
                )
                if wa_message_id and user_id:
                    account_resolved = state.get("account_resolved", {})

                    def save_confirmation():
                        with UnitOfWork() as uow:
                            if uow.actionable_messages:
                                uow.actionable_messages.create(
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
                                uow.commit()

                    try:
                        await asyncio.to_thread(save_confirmation)
                        logger.info("actionable_message_saved", message_type="transfer_confirmation")
                    except Exception as e:
                        logger.error("actionable_message_save_failed", error=str(e))

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

    source_accounts = state.get("source_accounts")
    use_dual_accounts = state.get("use_dual_accounts")
    explicit_split = state.get("explicit_split")

    if source_accounts:
        filtered = [a for a in accounts if a.get("bank_name") in source_accounts]
        if len(filtered) < len(source_accounts):
            missing = [b for b in source_accounts if b not in [a.get("bank_name") for a in filtered]]
            return {
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

    adapted_accounts = [AccountAdapter(a) for a in accounts]

    # If explicit split is provided, we use it after planning
    plan = await planner.plan_funding(
        accounts=adapted_accounts,
        transfer_amount=amount,
        preferred_account_id=preferred_account_id,
    )

    if not plan.is_sufficient:
        recipient_name = state.get("recipient_name", "")
        recipient_bank = state.get("recipient_bank_name", "")
        recipient_account = state.get("recipient_account", "")

        account_resolved = state.get("account_resolved")
        if account_resolved and isinstance(account_resolved, dict):
            recipient_name = account_resolved.get("account_name", recipient_name)

        selected_account = state.get("selected_source_account", {})
        primary_bank = selected_account.get("bank_name", "your account")
        primary_balance = plan.steps[0].amount if plan.steps else state.get("balance_available", 0)
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

        return {
            **state,
            "flow_state": "awaiting_amount_adjustment",
            "awaiting_confirmation": True,
            "funding_error": error_msg,
            "response": error_msg,
            "funding_status": "insufficient",
            "max_available": plan.total_funded,
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
        ],
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
        return {
            **state,
            "funding_plan": plan_dict,
            "funding_steps": plan_dict["steps"],
            "flow_state": "confirming_funding",
            "funding_status": "user_confirming",
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

    recipient_name = state.get("recipient_name", "")
    recipient_bank = state.get("recipient_bank_name", "")
    recipient_account = state.get("recipient_account", "")

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

    idem_key = uuid.uuid4().hex
    flow_token = f"transfer-pin-{idem_key}-{phone_number}"

    await redis_client.set(f"transfer:token:{idem_key}:phone", phone_number, ex=3600)

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
        "confirmation_token": idem_key,
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
        "response": "",
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

    logger.info(
        "verify_funding_approval_ENTRY",
        phone=phone_number,
        pin_verified_in_state=state.get("pin_verified"),
        funding_approved=state.get("funding_approved"),
        flow_state=state.get("flow_state"),
        confirmation_token=state.get("confirmation_token", "")[:20] if state.get("confirmation_token") else None,
    )

    token = state.get("confirmation_token")
    idempotency_key = token if token else state.get("idempotency_key", "")

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

    logger.info(
        "funding_verification_failed",
        phone=phone_number,
        used_key=idempotency_key,
        has_result=bool(pin_result),
        verified=pin_result.verified if pin_result else None,
    )

    if state.get("funding_approved"):
        logger.info("funding_approved_via_text_but_pin_missing", phone=phone_number)
        return {
            **state,
            "awaiting_confirmation": True,
            "funding_approved": False,  # Reset to prevent loop
            "response": "Please tap 'Authorize Funding' in the message above to confirm securely with your PIN.",
        }

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

            initiated_steps.append(
                {
                    **step,
                    "debit_id": result.debit_id,
                    "reference": reference,
                    "status": result.status.value if result.success else "failed",
                    "error": result.error_message,
                    "provider": direct_debit_provider.provider_name,  # Add provider name for database records
                }
            )

            logger.info(
                "debit_initiated",
                step=step["sequence"],
                debit_id=result.debit_id,
                success=result.success,
            )

        except Exception as e:
            logger.error("debit_initiation_failed", step=step["sequence"], error=str(e))
            initiated_steps.append(
                {
                    **step,
                    "status": "failed",
                    "error": str(e),
                }
            )

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

            updated_steps.append(
                {
                    **step,
                    "status": new_status,
                    "error": result.error_message if not result.success else None,
                }
            )

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
        logger.info(
            "wait_for_debits_ALL_SUCCESSFUL",
            flow_state="initiating_payout",
            funding_status="funded",
        )
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
