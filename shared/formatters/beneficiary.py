"""Beneficiary suggestion formatter."""


def format_beneficiary_suggestion(recipient_name: str) -> str:
    """Format beneficiary suggestion message."""
    return (
        f"💡 Would you like to save {recipient_name} as a beneficiary "
        f"for faster transfers? Reply to confirm."
    )
