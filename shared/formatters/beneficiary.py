"""Beneficiary suggestion formatter."""

from shared.i18n import render_message


def format_beneficiary_suggestion(recipient_name: str, locale: str = "en") -> str:
    """Format beneficiary suggestion message."""
    return render_message(
        "beneficiary.format.suggestion",
        locale,
        {"recipient_name": recipient_name},
    )
