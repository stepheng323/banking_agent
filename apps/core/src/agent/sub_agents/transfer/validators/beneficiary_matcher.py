"""Beneficiary matching component."""

from typing import Optional, Tuple
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiaryMatcher:
    """Matches recipient against saved beneficiaries."""

    def match(
        self,
        matched_beneficiary: Optional[dict],
        current_account: Optional[str],
        current_bank_code: Optional[str],
        current_recipient_name: Optional[str] = None,
    ) -> Tuple[bool, Optional[dict]]:
        """
        Check if matched beneficiary is valid for current recipient.

        Args:
            matched_beneficiary: Beneficiary dict from state
            current_account: Current recipient account number
            current_bank_code: Current recipient bank code
            current_recipient_name: Current recipient name (optional)

        Returns:
            Tuple of (use_beneficiary, resolved_account)
            - use_beneficiary: True if beneficiary data should be used
            - resolved_account: Account resolution dict if beneficiary is valid, None otherwise
        """
        if not matched_beneficiary or not isinstance(matched_beneficiary, dict):
            return False, None

        if not current_account or not current_bank_code:
            return False, None

        beneficiary_account = str(matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))

        if not (beneficiary_account and beneficiary_bank_code):
            logger.debug("beneficiary_missing_fields")
            return False, None

        if (str(current_account) != beneficiary_account or 
            str(current_bank_code) != beneficiary_bank_code):
            logger.debug(
                "beneficiary_stale",
                beneficiary_account=beneficiary_account,
                beneficiary_bank=beneficiary_bank_code,
                current_account=current_account,
                current_bank=current_bank_code,
            )
            return False, None

        logger.info(
            "beneficiary_matched",
            account=current_account,
            bank_code=current_bank_code,
        )

        resolved_account = {
            "success": True,
            "account_name": (
                current_recipient_name or
                matched_beneficiary.get("account_name") or
                matched_beneficiary.get("alias", "")
            ),
            "account_number": current_account,
            "bank_code": current_bank_code,
            "provider": "database",
        }

        return True, resolved_account
