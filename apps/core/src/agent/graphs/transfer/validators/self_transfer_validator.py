"""Self-transfer validation component."""

from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SelfTransferValidator:
    """Validates that recipient is not the same as source account."""

    def validate(
        self,
        recipient_account: str | None,
        recipient_bank_code: str | None,
        recipient_bank_name: str | None,
        source_account: dict | None,
    ) -> tuple[bool, str | None]:
        """
        Check if recipient matches source account.

        Args:
            recipient_account: Recipient account number
            recipient_bank_code: Recipient bank code
            recipient_bank_name: Recipient bank name
            source_account: Source account dict with account_number, bank_code, bank_name

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if recipient is different from source, False if same
            - error_message: Error message if invalid, None if valid
        """
        if not recipient_account or not source_account:
            return True, None

        source_account_number = source_account.get("account_number") or ""
        source_bank_name = source_account.get("bank_name") or ""
        source_bank_code = source_account.get("bank_code") or ""

        recipient_bank = (recipient_bank_name or "").lower().strip()
        source_bank = (source_bank_name or "").lower().strip()

        account_matches = recipient_account == source_account_number

        bank_matches = False
        if recipient_bank_code and source_bank_code:
            bank_matches = recipient_bank_code == source_bank_code
        elif recipient_bank and source_bank:
            bank_matches = recipient_bank == source_bank

        if account_matches and bank_matches:
            bank_identifier = recipient_bank_name or recipient_bank_code or "the same bank"
            error_message = (
                f"The recipient account ({recipient_account}) at {bank_identifier} "
                f"cannot be the same as your source account. Please provide a different recipient account."
            )
            logger.warning(
                "self_transfer_detected",
                recipient_account=recipient_account,
                recipient_bank=bank_identifier,
            )
            return False, error_message

        if account_matches and not bank_matches:
            logger.debug(
                "same_account_different_bank",
                account=recipient_account,
                recipient_bank=recipient_bank_name,
                source_bank=source_bank_name,
            )

        return True, None
