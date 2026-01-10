"""Transfer execution node - unified debit and payout logic.

Handles both single-account (direct-to-beneficiary) and multi-account (settlement + payout).
"""

from typing import Any

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def execute_single_account_transfer(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Execute transfer using Mono direct-to-beneficiary.

    One API call: debit from source → credit to recipient.
    """
    amount = state.get("amount", 0)
    selected_account = state.get("selected_source_account", {})
    recipient_account = state.get("recipient_account")
    recipient_bank_code = state.get("recipient_bank_code")
    idempotency_key = state.get("idempotency_key", "")

    mandate_id = selected_account.get("mandate_id")
    if not mandate_id:
        return {
            **state,
            "flow_state": "error",
            "transfer_status": "failed",
            "response": "Your account is not set up for transfers. Please complete mandate setup first.",
        }

    if not recipient_account or not recipient_bank_code:
        return {
            **state,
            "flow_state": "error",
            "transfer_status": "failed",
            "response": "Recipient account details missing.",
        }

    try:
        result = await direct_debit_provider.initiate_debit(
            mandate_id=mandate_id,
            amount=amount,
            reference=idempotency_key,
            narration=f"Transfer to {state.get('recipient_name', 'recipient')}" + (f" - {state['narration']}" if state.get("narration") else ""),
            beneficiary_account=recipient_account,
            beneficiary_bank_code=recipient_bank_code,
        )

        if result.success:
            return {
                **state,
                "flow_state": "awaiting_debits",
                "transfer_status": "authorized",
                "funding_status": "debiting",
                "funding_steps": [
                    {
                        "debit_id": result.debit_id,
                        "reference": idempotency_key,
                        "amount": amount,
                        "status": result.status.value,
                        "is_direct_to_beneficiary": True,
                    }
                ],
            }
        else:
            logger.error("single_account_transfer_failed", error=result.error_message)
            return {
                **state,
                "flow_state": "error",
                "transfer_status": "failed",
                "response": result.error_message or "Transfer failed. Please try again.",
            }

    except Exception as e:
        logger.error("single_account_transfer_exception", error=str(e))
        return {
            **state,
            "flow_state": "error",
            "transfer_status": "failed",
            "response": "Transfer failed due to a technical error. Please try again.",
        }


async def execute_multi_account_payout(
    state: TransferState,
    payment_provider: Any,
) -> TransferState:
    """
    Execute payout after multi-account debits are complete.

    Uses Flutterwave to send aggregated funds to recipient.
    """
    amount = state.get("amount", 0)
    recipient_account = state.get("recipient_account")
    recipient_bank_code = state.get("recipient_bank_code")
    recipient_name = state.get("recipient_name", "")
    idempotency_key = state.get("idempotency_key", "")
    
    base_narration = f"Transfer to {recipient_name}"
    narration = f"{base_narration} - {state['narration']}" if state.get("narration") else base_narration

    if not recipient_account or not recipient_bank_code:
        return {
            **state,
            "flow_state": "error",
            "transfer_status": "failed",
            "response": "Recipient account details missing.",
        }

    try:
        result = await payment_provider.initiate_transfer(
            amount=amount,
            account_number=recipient_account,
            bank_code=recipient_bank_code,
            narration=narration,
            reference=idempotency_key,
        )

        if result.get("success"):
            logger.info(
                "multi_account_payout_initiated",
                transfer_id=result.get("transfer_id"),
                amount=amount,
            )

            return {
                **state,
                "flow_state": "completed",
                "transfer_status": "completed",
                "funding_status": "completed",
            }
        else:
            logger.error("multi_account_payout_failed", error=result.get("error"))
            return {
                **state,
                "flow_state": "error",
                "transfer_status": "failed",
                "response": result.get("error")
                or "Payout failed. Your funds are safe and will be refunded.",
            }

    except Exception as e:
        logger.error("multi_account_payout_exception", error=str(e))
        return {
            **state,
            "flow_state": "error",
            "transfer_status": "failed",
            "response": "Payout failed due to a technical error. Your funds are safe.",
        }
