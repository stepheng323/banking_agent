"""Funding confirmation and verification.

Contains functions for confirming funding plans with users
and verifying PIN approval.
"""

import uuid
from typing import Any

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.formatters.transfer import format_funding_plan_summary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
        message_id=state.get("message_id"),
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
        "verify_funding_approval_entry",
        phone=phone_number,
        pin_verified_in_state=state.get("pin_verified"),
        funding_approved=state.get("funding_approved"),
        flow_state=state.get("flow_state"),
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
            "funding_approved": False,
            "response": "Please tap 'Authorize Funding' in the message above to confirm securely with your PIN.",
        }

    return state
