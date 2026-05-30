"""Transfer execution notification formatting."""

from __future__ import annotations

from typing import cast

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.personality import PersonalityContext, render_personalized_message
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount


def format_transfer_success_message(
    amount: MoneyAmount,
    recipient_name: str,
    transaction_id: str,
    locale: str = "en",
    personality_context: PersonalityContext | None = None,
) -> str:
    """Format transfer success notification message."""
    return cast(
        str,
        render_personalized_message(
            "transfer.format.notifications.success",
            locale,
            {
                "amount": format_naira(amount),
                "recipient_name": recipient_name,
                "transaction_id": transaction_id,
            },
            personality_context,
        ),
    )


def format_transfer_pending_message(
    amount: MoneyAmount,
    recipient_name: str,
    locale: str = "en",
    personality_context: PersonalityContext | None = None,
) -> str:
    """Format transfer pending notification message."""
    return cast(
        str,
        render_personalized_message(
            "transfer.format.notifications.pending",
            locale,
            {
                "amount": format_naira(amount),
                "recipient_name": recipient_name,
            },
            personality_context,
        ),
    )


def format_transfer_queued_message(
    amount: MoneyAmount,
    recipient_name: str,
    locale: str = "en",
) -> str:
    """Format the post-PIN acknowledgement before transfer processing."""
    return cast(
        str,
        render_message(
            "transfer.format.notifications.queued",
            locale,
            {"amount": format_naira(amount), "recipient_name": recipient_name},
        ),
    )
