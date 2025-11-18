"""Confirmation node for airtime purchase flow."""

import hashlib
import json
from typing import cast

from apps.core.src.agent.airtime.state import AirtimeState
from shared.clients.whatsapp_client import WhatsAppClient
from shared.config import settings
from shared.cache.redis_client import Redis

from ..graph.utils import debug_log


def _format_currency_naira(amount: float) -> str:
    """Format amount as Nigerian Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


async def prepare_confirmation(
    state: AirtimeState,
    whatsapp_client: WhatsAppClient,
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
                    "flow_state": "authorizing",
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

    recipient_display = recipient_name or recipient_phone or "Recipient"
    
    # Format summary with markdown formatting matching transfer style
    lines = [
        f"Amount: *{_format_currency_naira(float(amount or 0))}*",
        f"To: *{recipient_display}* ({network} - ```{recipient_phone}```)",
        f"From: {source_bank_name} (...{source_account_number[-4:] if source_account_number else '????'})",
    ]
    
    if narration:
        lines.append(f"Narration: _{narration}_")
    
    lines.append("")
    lines.append(
        "Tap the authorize button below to enter your transaction PIN.\n"
    )
    
    summary = "\n".join(lines)

    pending = {
        "phone": state["phone_number"],
        "amount": amount,
        "recipient": {
            "phone": recipient_phone,
            "network": network,
            "name": recipient_name,
        },
        "source": {
            "id": source.get("id") if source else None,
            "account_number": source_account_number,
            "account_name": (source.get("account_name") if source else None) or (source.get("name") if source else None),
            "bank_name": source_bank_name,
        },
        "narration": narration,
        "idempotency_key": idem_key,
        "status": "awaiting_confirmation",
        "transaction_type": "airtime",
    }

    token = f"transaction-pin-{idem_key}"
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
        f"transaction:token:{idem_key}:phone",
        900,
        state["phone_number"]
    )
    await pipe.execute()

    await whatsapp_client.send_flow(
        to=state["phone_number"],
        header="Confirm Your Airtime Purchase",
        flow_cta="Authorize Airtime",
        flow_id=settings.pin_confirmation_flow_id,
        screen_name="Pin",
        flow_token=token,
        text_body=summary,
    )

    return cast(AirtimeState, {
        **state,
        "response": "", 
        "idempotency_key": idem_key,
        "airtime_status": "pending",
        "flow_state": "authorizing",
    })
