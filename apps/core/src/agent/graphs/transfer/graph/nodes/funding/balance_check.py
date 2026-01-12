"""Balance checking for transfer funding.

Contains the check_funding function that verifies if the selected
source account has sufficient balance for the transfer.
"""

import asyncio
from datetime import datetime, timedelta

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.formatters.transfer import format_transfer_summary
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.unit_of_work import UnitOfWork
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

    logger.info("check_funding_entry", amount=amount, selected_account=selected_account)

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

        # Handle transfer_all and transfer_percentage
        state, amount = _handle_dynamic_amount(state, balance, amount)

        if balance >= (amount or 0):
            return await _handle_sufficient_balance(state, balance, whatsapp_client)
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


def _handle_dynamic_amount(state: TransferState, balance: float, amount: float) -> tuple[TransferState, float]:
    """Handle transfer_all and transfer_percentage amount calculations."""
    transfer_all = state.get("transfer_all")
    transfer_percentage = state.get("transfer_percentage")

    if transfer_all and not amount:
        amount = balance
        logger.info("transfer_all_amount_set", balance=balance, amount=amount)
        state = _regenerate_confirmation_summary(state, amount)

    if transfer_percentage:
        amount = balance * (transfer_percentage / 100)
        logger.info(
            "transfer_percentage_calculated",
            percentage=transfer_percentage,
            balance=balance,
            calculated_amount=amount,
        )
        state = _regenerate_confirmation_summary(state, amount, transfer_percentage)

    return state, amount


def _regenerate_confirmation_summary(
    state: TransferState, amount: float, percentage: float | None = None
) -> TransferState:
    """Regenerate confirmation summary with calculated amount."""
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

    summary_data = {
        "amount": amount,
        "recipientName": rec_name,
        "recipientBank": bank_name,
        "recipientAccount": acct_number,
        "sourceBank": source_bank_name,
        "sourceAccount": source_account_number,
        "narration": narration,
    }
    if percentage:
        summary_data["percentage"] = percentage

    new_summary = format_transfer_summary(summary_data)
    return {**state, "amount": amount, "confirmation_summary": new_summary}


async def _handle_sufficient_balance(
    state: TransferState,
    balance: float,
    whatsapp_client: WhatsAppClient,
) -> TransferState:
    """Handle case when balance is sufficient - send PIN confirmation flow."""
    token = state.get("confirmation_token", "")
    summary = state.get("confirmation_summary", "")

    logger.info(
        "check_funding_token_summary_check",
        has_token=bool(token),
        has_summary=bool(summary),
    )

    if token and summary:
        flow_result = await whatsapp_client.send_flow(
            to=state["phone_number"],
            header="Confirm Your Transfer",
            flow_cta="Authorize Transfer",
            flow_id=settings.pin_confirmation_flow_id,
            screen_name="Pin",
            flow_token=token,
            text_body=summary,
            message_id=state.get("message_id"),
        )

        wa_message_id = flow_result.get("messages", [{}])[0].get("id", "")
        user_id = state.get("user_profile", {}).get("id")

        if wa_message_id and user_id:
            await _save_actionable_message(state, wa_message_id, user_id)

    return {
        **state,
        "balance_available": balance,
        "funding_required": False,
        "flow_state": "authorizing",
    }


async def _save_actionable_message(state: TransferState, wa_message_id: str, user_id: str) -> None:
    """Save actionable message for repeat/modify functionality."""
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
