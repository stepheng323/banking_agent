"""Account validation component."""

import asyncio
from shared.utils.async_helpers import create_background_task
from typing import Optional, Tuple
from apps.core.src.agent.tools.validation.service import AsyncValidationService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountValidator:
    """Validates recipient account via payment provider API."""

    def __init__(
        self,
        validation_service: AsyncValidationService,
        whatsapp_client: Optional[WhatsAppClient] = None,
    ):
        """
        Initialize validator.

        Args:
            validation_service: Service for account validation
            whatsapp_client: Optional WhatsApp client for sending acknowledgments
        """
        self.validation_service = validation_service
        self.whatsapp_client = whatsapp_client

    async def validate(
        self,
        account_number: str,
        bank_code: str,
        source_account_id: str,
        phone_number: Optional[str] = None,
    ) -> Tuple[Optional[dict], Optional[dict]]:
        """
        Validate account and check balance.

        Args:
            account_number: Recipient account number
            bank_code: Recipient bank code
            source_account_id: Source account ID for balance check
            phone_number: Optional phone number for sending acknowledgment

        Returns:
            Tuple of (resolved_account, balance)
            - resolved_account: Account resolution result or None if failed
            - balance: Balance info or None if not available
        """
        if self.whatsapp_client and phone_number:
            try:
                create_background_task(
                    self.whatsapp_client.send_text(
                        phone_number,
                        "🔍 Validating account details..."
                    )
                )
            except Exception as e:
                logger.warning("validation_ack_failed", error=str(e))

        logger.debug(
            "account_validation_started",
            account=account_number,
            bank_code=bank_code,
        )

        resolved, balance = await self.validation_service.validate_account_and_balance(
            account_number=str(account_number),
            bank_code=str(bank_code),
            source_account_id=str(source_account_id),
        )

        is_success = resolved and isinstance(resolved, dict) and resolved.get("success", False)
        if is_success:
            logger.info(
                "account_validation_success",
                account=account_number,
                account_name=resolved.get("account_name"),
            )
        else:
            error_msg = resolved.get("error") if isinstance(resolved, dict) else None
            logger.warning(
                "account_validation_failed",
                account=account_number,
                bank_code=bank_code,
                error=error_msg,
            )

        return resolved, balance
