"""Confirmation node for airtime purchase flow."""

import hashlib
import json
from typing import cast

from apps.core.src.agent.airtime.state import AirtimeState
from shared.clients.whatsapp_client import WhatsAppClient
from shared.cache.redis_client import Redis

from ..graph.utils import debug_log


async def prepare_confirmation(
    state: AirtimeState,
    whatsapp_client: WhatsAppClient,  # noqa: ARG001  # Reserved for future WhatsApp flow integration
    redis_client: Redis,
) -> AirtimeState:
    """Prepare airtime purchase confirmation summary."""
    debug_log(
        f"DEBUG prepare_confirmation: amount={state.get('amount')}, recipient_phone={state.get('recipient_phone')}, network={state.get('network')}")

    phone_number = state.get("phone_number")
    existing_idem_key = state.get("idempotency_key")
    if existing_idem_key:
        pending_data = await redis_client.get(f"user:{phone_number}:pending_airtime")
        if pending_data:
            pending_airtime = json.loads(pending_data)
            if pending_airtime.get("idempotency_key") == existing_idem_key:
                debug_log(
                    f"✅ Airtime confirmation flow already sent for idem_key: {existing_idem_key}")
                return cast(AirtimeState, {
                    **state,
                    "response": "",
                    "airtime_status": "pending",
                    "flow_state": "confirming",
                })

    amount = state.get("amount")
    recipient_phone = state.get("recipient_phone")
    network = state.get("network")
    source = state.get("selected_source_account", {})
    narration = state.get("narration")
    recipient_name = state.get("recipient_name")

    idem_key = state.get("idempotency_key")
    if not idem_key:
        idem_key = hashlib.sha256(
            f"{state['phone_number']}|{amount}|{recipient_phone}|{network}".encode(
                "utf-8")
        ).hexdigest()

    source_account_number = (source.get(
        "account_number") if source else None) or ""
    source_bank_name = (source.get("bank_name") if source else None) or (
        source.get("name") if source else None) or "Account"

    # Format confirmation message
    recipient_display = recipient_name or recipient_phone or "Recipient"
    summary = "📱 Airtime Purchase Summary\n\n"
    summary += f"Amount: ₦{amount:,.2f}\n"
    summary += f"Recipient: {recipient_display} ({network})\n"
    summary += f"Phone: {recipient_phone}\n"
    summary += f"Source: {source_bank_name} • {source_account_number}\n"
    if narration:
        summary += f"Note: {narration}\n"
    summary += "\nReply with your PIN to confirm."

    pending = {
        "phone": state["phone_number"],
        "amount": amount,
        "recipient": {
            "phone": recipient_phone,
            "network": network,
            "name": recipient_name,
        },
        "source": {
            # type: ignore[union-attr]
            "id": source.get("id") if source else None,
            "account_number": source_account_number,
            # type: ignore[union-attr]
            "account_name": (source.get("account_name") if source else None) or (source.get("name") if source else None),
            "bank_name": source_bank_name,
        },
        "narration": narration,
        "idempotency_key": idem_key,
        "status": "awaiting_confirmation",
    }

    # Store pending airtime purchase in Redis
    token = f"airtime-pin-{idem_key}"
    pipe = redis_client.pipeline()
    pipe.setex(
        f"user:{state['phone_number']}:pending_airtime",
        900,
        json.dumps(pending)
    )
    pipe.setex(
        f"user:{state['phone_number']}:pending_airtime_flow_token",
        900,
        token
    )
    pipe.setex(
        f"airtime:token:{idem_key}:phone",
        900,
        state["phone_number"]
    )
    await pipe.execute()

    return cast(AirtimeState, {
        **state,
        "response": summary,
        "idempotency_key": idem_key,
        "airtime_status": "pending",
        "flow_state": "confirming",
    })
