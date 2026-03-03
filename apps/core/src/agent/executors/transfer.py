"""Transfer Executor.

Handles execution of transfer transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.repositories.account_repository import AccountRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferExecutor:
    """Executor for Transfer transactions."""

    def __init__(
        self,
        direct_debit_provider: DirectDebitProvider,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
    ):
        self.direct_debit_provider = direct_debit_provider
        self.account_repo = account_repo
        self.transaction_repo = transaction_repo

    async def handle_transfer(self, data: dict[str, Any]) -> None:
        """Handle execution of a transfer transaction."""
        transaction_id = data.get("transaction_id")
        transfer_data = data.get("transfer_data", {})
        locale = data.get("language", "en")

        if not transaction_id:
            logger.error("transfer_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_transfer", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = transfer_data.get("amount")
            recipient = transfer_data.get("recipient", {})
            source = transfer_data.get("source", {})
            narration = transfer_data.get("narration")
            source_account_id = source.get("account_id")
            recipient_account = recipient.get("account_number")
            recipient_bank_code = recipient.get("bank_code")
            reference = str(data.get("idempotency_key") or transaction_id)

            if not source_account_id:
                raise ValueError("missing_source_account_id")
            if not recipient_account or not recipient_bank_code:
                raise ValueError("missing_recipient_account_details")
            if not amount or float(amount) <= 0:
                raise ValueError("invalid_transfer_amount")
            amount_value = float(amount)

            source_account = await self.account_repo.get_by_id(str(source_account_id))
            if not source_account or not source_account.mandate_id:
                raise ValueError("source_account_mandate_not_ready")

            result = await self.direct_debit_provider.initiate_debit_to_beneficiary(
                amount=amount_value,
                mandate_id=source_account.mandate_id,
                reference=reference,
                beneficiary_account=str(recipient_account),
                beneficiary_bank_code=str(recipient_bank_code),
                narration=str(narration or "Transfer"),
            )

            if result.success and result.status == DebitStatus.SUCCESSFUL:
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.SUCCESSFUL.value,
                )
                logger.info("transfer_success", transaction_id=transaction_id, ref=result.reference)
            elif result.success and result.status in (DebitStatus.PENDING, DebitStatus.PROCESSING):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.PROCESSING.value,
                )
                logger.info("transfer_processing", transaction_id=transaction_id, ref=result.reference)
            else:
                error_msg = result.error_message or render_message("transfer.error.provider_failed", locale)
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.FAILED.value,
                    error_message=error_msg,
                )
                logger.error("transfer_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("transfer_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
