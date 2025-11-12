"""Confirmation node for transfer flow."""

from typing import cast
import hashlib
import json
import redis.asyncio as redis

from apps.core.src.agent.formatters.transfer import format_transfer_summary
from apps.core.src.agent.transfer.state import TransferState
from shared.clients.whatsapp_client import WhatsAppClient
from shared.config import settings

from .utils import debug_log


async def prepare_confirmation(
    state: TransferState,
    whatsapp_client: WhatsAppClient,
    redis_client: redis.Redis,
) -> TransferState:
    """Prepare transfer confirmation summary."""
    debug_log(
        f"DEBUG prepare_confirmation: state={json.dumps(state, indent=2)}")

    phone_number = state.get("phone_number")
    existing_idem_key = state.get("idempotency_key")
    if existing_idem_key:
        pending_data = await redis_client.get(f"user:{phone_number}:pending_transfer")
        if pending_data:
            pending_transfer = json.loads(pending_data)
            if pending_transfer.get("idempotency_key") == existing_idem_key:
                debug_log(
                    f"✅ Transfer confirmation flow already sent for idem_key: {existing_idem_key}")
                return {
                    **state,
                    "response": "",
                    "transfer_status": "pending",
                    "flow_state": "confirming",
                }

    amount = state.get("amount")
    account_resolved = state.get("account_resolved")
    rec_name = (
        account_resolved.get("account_name") if account_resolved and isinstance(account_resolved, dict)
        else state.get("recipient_name") or "Recipient"
    )
    bank_name = state.get("recipient_bank_name") or state.get(
        "recipient_bank_code") or ""
    acct_number = state.get("recipient_account")
    source = state.get("selected_source_account", {})
    narration = state.get("narration")

    idem_key = state.get("idempotency_key")
    if not idem_key:
        idem_key = hashlib.sha256(
            f"{state['phone_number']}|{amount}|{acct_number}|{bank_name}".encode(
                "utf-8")
        ).hexdigest()

    source_account_number = source.get("account_number") or ""
    source_bank_name = source.get(
        "bank_name") or source.get("name") or "Account"

    summary = format_transfer_summary({
        "amount": float(amount or 0),
        "recipientName": rec_name,
        "recipientBank": bank_name,
        "recipientAccount": str(acct_number),
        "sourceBank": source_bank_name,
        "sourceAccount": source_account_number,
        "narration": narration,
    })

    pending = {
        "phone": state["phone_number"],
        "amount": amount,
        "recipient": {
            "name": rec_name,
            "account_number": acct_number,
            "bank_code": state.get("recipient_bank_code"),
            "bank_name": bank_name,
        },
        "source": {
            "id": source.get("id"),
            "account_number": source_account_number,
            "account_name": source.get("account_name") or source.get("name"),
            "bank_name": source_bank_name,
        },
        "narration": narration,
        "idempotency_key": idem_key,
        "status": "awaiting_confirmation",
    }

    # OPTIMIZED: Batch Redis SET operations using pipeline
    token = f"transfer-pin-{idem_key}"
    pipe = redis_client.pipeline()
    pipe.setex(
        f"user:{state['phone_number']}:pending_transfer",
        900,
        json.dumps(pending)
    )
    pipe.setex(
        f"user:{state['phone_number']}:pending_transfer_flow_token",
        900,
        token
    )
    pipe.setex(
        f"transfer:token:{idem_key}:phone",
        900,
        state["phone_number"]
    )
    await pipe.execute()

    # Store initial previous values if not already stored
    prev_key = f"transfer:prev:{state['phone_number']}:{idem_key}"
    prev_data = await redis_client.get(prev_key)
    if not prev_data:
        prev_values = {
            "amount": amount,
            "recipient_account": acct_number,
            "recipient_bank_code": state.get("recipient_bank_code"),
            "recipient_bank_name": bank_name,
            "recipient_name": rec_name,
        }
        await redis_client.set(
            prev_key,
            json.dumps(prev_values),
            ex=3600
        )

    await whatsapp_client.send_flow(
        to=state["phone_number"],
        header="Confirm Your Transfer",
        flow_cta="Authorize Transfer",
        flow_id=settings.pin_confirmation_flow_id,
        screen_name="Pin",
        flow_token=token,
        text_body=summary,
    )

    return {
        **state,
        "response": "",
        "idempotency_key": idem_key,
        "transfer_status": "pending",
        "flow_state": "confirming",
    }

