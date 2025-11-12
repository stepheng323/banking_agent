"""Beneficiary suggestion service."""


from shared.repositories.beneficiary_repository import BeneficiaryRepository


def should_suggest_beneficiary(
    user_id: str,
    account_number: str,
    bank_code: str,
    beneficiary_repo: BeneficiaryRepository,
) -> bool:
    """
    Check if recipient should be suggested as a beneficiary.

    Args:
        user_id: User's ID
        account_number: Recipient account number
        bank_code: Recipient bank code
        beneficiary_repo: Beneficiary repository instance

    Returns:
        True if recipient is not already a beneficiary and should be suggested
    """
    try:
        beneficiaries = beneficiary_repo.get_by_user(user_id)

        for beneficiary in beneficiaries:
            if (
                beneficiary.account_number == account_number
                and beneficiary.bank_code == bank_code
            ):
                return False  # Already a beneficiary

        return True  # New recipient, suggest saving
    except Exception:
        return False


def format_beneficiary_suggestion(recipient_name: str) -> str:
    """
    Format beneficiary suggestion message.

    Args:
        recipient_name: Name of the recipient

    Returns:
        Formatted suggestion message
    """
    return (
        f"💡 Would you like to save {recipient_name} as a beneficiary "
        f"for faster transfers? Reply to confirm."
    )
