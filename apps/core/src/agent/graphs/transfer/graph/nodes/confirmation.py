"""Confirmation node for transfer flow."""

import json

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import Redis
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.formatters.transfer import format_transfer_summary
from shared.utils.logging import get_logger


async def prepare_confirmation(
    state: TransferState,
    whatsapp_client: WhatsAppClient,
    redis_client: Redis,
) -> TransferState:
    """Prepare transfer confirmation summary."""

    logger = get_logger(__name__)

    phone_number = state.get("phone_number")
    logger.info(
        "prepare_confirmation_ENTRY",
        phone_number=phone_number,
        flow_state=state.get("flow_state"),
        skip_confirmation_display=state.get("skip_confirmation_display"),
    )

    if state.get("skip_confirmation_display"):
        return {
            **state,
            "response": "",
            "skip_confirmation_display": False,
        }

    queue_key = f"user:{phone_number}:task_queue"
    has_active_queue = await redis_client.exists(queue_key)

    existing_idem_key = state.get("idempotency_key")
    if existing_idem_key:
        pending_data = await redis_client.get(f"user:{phone_number}:pending_transfer")
        if pending_data:
            pending_transfer = json.loads(pending_data)
            if pending_transfer.get("idempotency_key") == existing_idem_key:
                if has_active_queue:
                    return {
                        **state,
                        "response": "",
                        "transfer_status": "collection_complete",
                        "flow_state": "confirming",
                    }
                return {
                    **state,
                    "response": "",
                    "transfer_status": "pending",
                    "flow_state": "confirming",
                }

    amount = state.get("amount")
    account_resolved = state.get("account_resolved")
    rec_name = (
        account_resolved.get("account_name")
        if account_resolved and isinstance(account_resolved, dict)
        else state.get("recipient_name") or "Recipient"
    )
    bank_name = state.get("recipient_bank_name") or state.get("recipient_bank_code") or ""
    acct_number = state.get("recipient_account")
    source = state.get("selected_source_account", {})
    narration = state.get("narration")

    idem_key = state.get("idempotency_key")
    if not idem_key:
        import uuid
        idem_key = str(uuid.uuid4())

    source_account_number = source.get("account_number") or ""
    source_bank_name = source.get("bank_name") or source.get("name") or "Account"

    summary = format_transfer_summary(
        {
            "amount": float(amount or 0),
            "recipientName": rec_name,
            "recipientBank": bank_name,
            "recipientAccount": str(acct_number),
            "sourceBank": source_bank_name,
            "sourceAccount": source_account_number,
            "narration": narration,
        }
    )

    {
        "phone": state["phone_number"],
        "amount": amount,
        "recipient": {
            "name": rec_name,
            "account_number": acct_number,
            "bank_code": state.get("recipient_bank_code"),
            "bank_name": bank_name,
            "original_alias": state.get("recipient_name") or "",
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

    token = f"transfer-pin-{idem_key}-{state['phone_number']}"
    pipe = redis_client.pipeline()
    pipe.setex(
        f"user:{state['phone_number']}:pending_transfer_flow_token",
        settings.pending_transaction_ttl,
        token,
    )
    pipe.setex(f"transfer:token:{idem_key}:phone", settings.pending_transaction_ttl, state["phone_number"])
    await pipe.execute()

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
        await redis_client.set(prev_key, json.dumps(prev_values), ex=3600)

    if has_active_queue:
        logger.info(
            "prepare_confirmation_RETURNING",
            has_token=bool(token),
            has_summary=bool(summary),
            token_preview=token[:20] if token else "NONE",
            has_active_queue=True,
        )
        return {
            **state,
            "idempotency_key": idem_key,
            "transfer_status": "collection_complete",
            "flow_state": "confirming",
            "confirmation_summary": summary,
            "confirmation_token": token,
            "response": "",
            "llm_reply": None,
        }
    logger.info("prepare_confirmation_RETURNING_FUNDING", has_token=bool(token), has_active_queue=False)

    return {
        **state,
        "idempotency_key": idem_key,
        "transfer_status": "pending",
        "flow_state": "confirming_funding",
        "funding_required": True,
        "confirmation_summary": summary,
        "confirmation_token": token,
        "_amount_at_confirmation": amount,
        "response": "",  # Clear stale response
        "llm_reply": None,
    }
