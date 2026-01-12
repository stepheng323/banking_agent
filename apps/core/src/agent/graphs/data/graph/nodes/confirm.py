"""Confirmation node for data purchase flow - triggers PIN verification."""

import uuid
from typing import Any

from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from shared.cache.redis_client import Redis
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def confirm_node(
    state: DataPurchaseState,
    redis_client: Redis,
    whatsapp_client: WhatsAppClient,
) -> dict[str, Any]:
    """
    Confirm data purchase and trigger PIN verification.

    This node:
    1. Generates idempotency key
    2. Sends confirmation message with PIN flow
    3. Returns state in 'authorizing' status
    """
    phone_number = state.get("phone_number", "")
    selected_plan = state.get("selected_plan") or state.get("suggested_plan")

    if not selected_plan:
        return {
            "flow_state": "error",
            "response": "No plan selected. Please try again.",
        }

    idem_key = state.get("idempotency_key") or str(uuid.uuid4())

    # Format confirmation message
    plan_name = selected_plan.name
    amount = selected_plan.amount
    target_phone = state.get("target_phone", "")
    source = state.get("source", "self")

    target_display = "your number" if source == "self" else target_phone

    confirmation_msg = (
        f"📱 *Data Purchase Confirmation*\n\n"
        f"Plan: {plan_name}\n"
        f"Amount: ₦{amount:,.0f}\n"
        f"To: {target_display}\n\n"
        f"Please enter your PIN to confirm."
    )

    try:
        await whatsapp_client.send_whatsapp_flow(
            phone_number=phone_number,
            flow_id="pin_verification",
            flow_token=idem_key,
            flow_action="navigate",
            flow_action_payload={
                "screen": "PIN_SCREEN",
                "data": {
                    "flow_type": "data",
                    "idempotency_key": idem_key,
                    "amount": amount,
                    "description": f"Data: {plan_name}",
                },
            },
            header_text="Confirm Purchase",
            body_text=confirmation_msg,
            cta_text="Enter PIN",
        )

        logger.info(
            "data_pin_flow_sent",
            phone=phone_number,
            idem_key=idem_key[:20],
            plan=plan_name,
        )

        return {
            "flow_state": "authorizing",
            "idempotency_key": idem_key,
            "response": "",  # WhatsApp Flow handles the UI
        }

    except Exception as e:
        logger.error("data_pin_flow_error", error=str(e), exc_info=True)
        return {
            "flow_state": "error",
            "response": "Failed to start verification. Please try again.",
        }
