"""Confirmation node for airtime purchase flow."""

import hashlib
import json
from typing import cast

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.cache.redis_client import Redis
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from datetime import datetime, timedelta
from typing import Optional

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
    actionable_message_repo: Optional[ActionableMessageRepository] = None,
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
                    f"✓ Airtime confirmation flow already sent for idem_key: {existing_idem_key}")
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

    # Build recipient lines - cleaner format for mobile
    if recipient_name and recipient_name != recipient_phone:
        recipient_lines = [
            f"*To:* {recipient_name}",
            f"*Phone:* `{recipient_phone}`",
            f"*Network:* {network}",
        ]
    else:
        recipient_lines = [
            f"*To:* `{recipient_phone}`",
            f"*Network:* {network}",
        ]
    
    lines = [
        f"*Amount:* {_format_currency_naira(float(amount or 0))}",
        *recipient_lines,
    ]
    
    if narration:
        lines.append(f"*Note:* {narration}")
    
    # Separator between recipient and source
    lines.append("")
    lines.append(f"*From:* {source_bank_name} (···{source_account_number[-4:] if source_account_number else '????'})")
    lines.append("")
    lines.append("Tap *Authorize* to enter your PIN.")
    
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

    token = f"transaction-pin-{idem_key}-{state['phone_number']}"
    pipe = redis_client.pipeline()
    pipe.setex(
        f"user:{state['phone_number']}:pending_airtime",
        settings.pending_transaction_ttl,
        json.dumps(pending)
    )
    pipe.setex(
        f"user:{state['phone_number']}:pending_airtime_flow_token",
        settings.pending_transaction_ttl,
        token
    )
    pipe.setex(
        f"transaction:token:{idem_key}:phone",
        settings.pending_transaction_ttl,
        state["phone_number"]
    )
    await pipe.execute()

    # Add typing indicator before showing flow
    if state.get("message_id"):
        await whatsapp_client.send_typing_indicator(state["message_id"])
        await asyncio.sleep(0.3)  # Allow WhatsApp to render typing indicator

    flow_result = await whatsapp_client.send_flow(
        to=state["phone_number"],
        header="Confirm Your Airtime Purchase",
        flow_cta="Authorize Airtime",
        flow_id=settings.pin_confirmation_flow_id,
        screen_name="Pin",
        flow_token=token,
        text_body=summary,
    )
    
    if actionable_message_repo:
        wa_message_id = flow_result.get("messages", [{}])[0].get("id", "")
        user_id = state.get("user_profile", {}).get("id")
        if wa_message_id and user_id:
            actionable_message_repo.create(
                user_id=user_id,
                wa_message_id=wa_message_id,
                message_type="airtime_confirmation",
                message_data={
                    "amount": amount,
                    "phone_number": recipient_phone,
                    "network": network,
                    "recipient_name": recipient_name,
                },
                expires_at=datetime.utcnow() + timedelta(days=90),
            )

    return cast(AirtimeState, {
        **state,
        "response": "", 
        "idempotency_key": idem_key,
        "airtime_status": "pending",
        "flow_state": "authorizing",
    })
