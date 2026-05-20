"""Helpers for consent-based receipt image generation."""

from __future__ import annotations

from typing import Any

from shared.i18n import render_message
from shared.messaging.intents import ShowOptions

RECEIPT_IMAGE_ACTION_ID = "rcpt:send"
RECEIPT_IMAGE_ACTION = "generate_receipt_image"
RECEIPT_IMAGE_ACTION_TTL_DAYS = 3650


def parse_receipt_choice_action(text: str) -> bool:
    return (text or "").strip() == RECEIPT_IMAGE_ACTION_ID


def receipt_choice_claim_key(channel: str, message_id: str) -> str:
    normalized_channel = (channel or "unknown").strip() or "unknown"
    normalized_message_id = (message_id or "unknown").strip() or "unknown"
    return f"receipt:image-choice-claim:{normalized_channel}:{normalized_message_id}"


def build_receipt_choice_actionable_payload(job: dict[str, Any]) -> dict[str, Any]:
    transaction_reference = job.get("transaction_reference")
    return {
        "action": RECEIPT_IMAGE_ACTION,
        "transaction_id": str(transaction_reference) if transaction_reference else None,
        "receipt_job": job,
        "actionable_ttl_days": RECEIPT_IMAGE_ACTION_TTL_DAYS,
    }


def extract_receipt_choice_job(actionable_payload: Any) -> dict[str, Any] | None:
    if not isinstance(actionable_payload, dict):
        return None
    if actionable_payload.get("action") != RECEIPT_IMAGE_ACTION:
        return None
    receipt_job = actionable_payload.get("receipt_job")
    return dict(receipt_job) if isinstance(receipt_job, dict) else None


def build_receipt_choice_intent(job: dict[str, Any], locale: str) -> ShowOptions:
    return ShowOptions(
        title=render_message("query.receipt.offer", locale),
        options=[
            {
                "id": RECEIPT_IMAGE_ACTION_ID,
                "title": render_message("query.receipt.offer_accept", locale),
                "button_title": render_message("query.receipt.offer_accept_button", locale),
            },
        ],
        actionable_payload=build_receipt_choice_actionable_payload(job),
    )
