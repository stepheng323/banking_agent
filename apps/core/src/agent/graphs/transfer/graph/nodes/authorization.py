"""Authorization node for transfer flow using shared base."""

from datetime import datetime
from typing import Any, cast

from apps.core.src.agent.graphs.__shared__.authorization import AuthorizationBase
from apps.core.src.agent.graphs.__shared__.response import ResponseIntent
from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import Redis
from shared.database.connection import get_db
from shared.database.models import (
    FundedTransfer,
    FundedTransferStatusEnum,
    FundingStep,
    FundingStepStatusEnum,
)
from shared.formatters.transfer import format_transfer_queued_message
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.auth import AuthorizationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferAuthorization(AuthorizationBase[TransferState]):
    """Transfer-specific authorization extending shared base."""

    def get_flow_type(self) -> str:
        return "transfer"

    def get_transaction_type(self) -> str:
        return "transfer"

    def get_status_field(self) -> str:
        return "transfer_status"

    def get_pending_data_key(self, phone_number: str) -> str | None:
        return f"user:{phone_number}:pending_transfer"

    def get_error_intent(self) -> ResponseIntent:
        return ResponseIntent.TRANSFER_FAILED

    def build_transaction_params(self, state: TransferState, user_id: str) -> dict[str, Any]:
        """Build transfer transaction parameters."""
        amount = state.get("amount")
        recipient_account = state.get("recipient_account")
        recipient_bank_name = state.get("recipient_bank_name")
        recipient_bank_code = state.get("recipient_bank_code")
        recipient_name = state.get("recipient_name")

        if not amount or not recipient_account:
            raise ValueError("Missing required transfer data (amount or recipient)")

        source_account = state.get("selected_source_account", {})
        sender_name = source_account.get("account_name") or source_account.get("name", "")

        narration = state.get("narration")
        if not narration:
            narration = (
                f"{sender_name.upper()} transfer to {recipient_name.upper()}"
                if sender_name
                else f"Transfer to {recipient_name}"
            )

        return {
            "amount": float(amount),
            "currency": "NGN",
            "source_account_id": source_account.get("id"),
            "source_account_number": source_account.get("account_number", ""),
            "source_bank_name": source_account.get("bank_name", ""),
            "recipient_account_number": recipient_account,
            "recipient_bank_code": recipient_bank_code or "",
            "recipient_bank_name": recipient_bank_name or "",
            "recipient_name": recipient_name or "",
            "narration": narration,
        }

    async def enqueue_for_execution(
        self,
        state: TransferState,
        transaction_id: str,
        transaction_params: dict[str, Any],
        user_id: str,
    ) -> None:
        """Enqueue transfer for execution, handling funded transfers."""
        idem_key = state.get("idempotency_key")
        phone_number = state.get("phone_number")

        funded_transfer_id = await self._create_funded_transfer_if_needed(state, transaction_params, user_id, idem_key)

        if funded_transfer_id:
            self._link_funded_transfer(transaction_id, funded_transfer_id)

        source_account = state.get("selected_source_account", {})
        transfer_data = {
            "amount": transaction_params["amount"],
            "recipient": {
                "account_number": transaction_params["recipient_account_number"],
                "bank_name": transaction_params["recipient_bank_name"],
                "bank_code": transaction_params["recipient_bank_code"],
                "name": transaction_params["recipient_name"],
            },
            "source": {
                "id": source_account.get("id"),
                "account_number": source_account.get("account_number", ""),
                "account_name": source_account.get("account_name") or source_account.get("name", ""),
                "bank_name": source_account.get("bank_name", ""),
            },
            "narration": transaction_params["narration"],
            "idempotency_key": idem_key,
            "user_id": user_id,
        }

        await self.queue.enqueue_simple(
            queue_name="banking:transactions",
            message={
                "type": "execute_transfer",
                "phone_number": phone_number,
                "idempotency_key": idem_key,
                "transfer_data": transfer_data,
                "transaction_id": transaction_id,
            },
        )

    def build_success_response(self, state: TransferState, transaction_id: str, retry_count: int) -> TransferState:
        """Build success response with queued message."""
        amount = state.get("amount", 0)
        recipient_name = state.get("recipient_name", "recipient")

        queued_message = format_transfer_queued_message(
            amount=float(amount),
            recipient_name=recipient_name,
        )

        return cast(
            TransferState,
            {
                **state,
                "response": queued_message,
                "flow_state": "completed",
                "transfer_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": retry_count,
            },
        )

    async def _create_funded_transfer_if_needed(
        self,
        state: TransferState,
        params: dict[str, Any],
        user_id: str,
        idem_key: str,
    ) -> str | None:
        """Create FundedTransfer record for multi-account transfers."""
        funding_required = state.get("funding_required", False)
        funding_steps = state.get("funding_steps", [])

        if not funding_required or not funding_steps:
            return None

        db = next(get_db())
        try:
            funded_transfer = FundedTransfer(
                user_id=user_id,
                amount=params["amount"],
                recipient_account_number=params["recipient_account_number"],
                recipient_bank_code=params["recipient_bank_code"],
                recipient_bank_name=params["recipient_bank_name"],
                recipient_name=params["recipient_name"],
                idempotency_key=idem_key,
                status=FundedTransferStatusEnum.PAYOUT_PENDING.value,
            )
            db.add(funded_transfer)
            db.flush()
            funded_transfer_id = str(funded_transfer.id)

            for idx, step in enumerate(funding_steps, start=1):
                step_status = step.get("status", "pending")
                funding_step_status = self._map_step_status(step_status)

                funding_step = FundingStep(
                    funded_transfer_id=funded_transfer.id,
                    account_id=step.get("account_id"),
                    amount=step.get("amount"),
                    sequence=idx,
                    provider_name=step.get("provider", "mono"),
                    provider_debit_id=step.get("debit_id"),
                    status=funding_step_status,
                    initiated_at=datetime.utcnow(),
                    confirmed_at=datetime.utcnow() if step_status == "successful" else None,
                    error_message=step.get("error"),
                )
                db.add(funding_step)

            db.commit()
            logger.info(
                "funded_transfer_created",
                funded_transfer_id=funded_transfer_id,
                num_steps=len(funding_steps),
            )
            return funded_transfer_id

        except Exception as e:
            db.rollback()
            logger.error("funded_transfer_creation_failed", error=str(e), exc_info=True)
            return None
        finally:
            db.close()

    def _map_step_status(self, step_status: str) -> str:
        """Map step status string to enum value."""
        mapping = {
            "successful": FundingStepStatusEnum.CONFIRMED.value,
            "failed": FundingStepStatusEnum.FAILED.value,
            "pending": FundingStepStatusEnum.PENDING.value,
        }
        return mapping.get(step_status, step_status)

    def _link_funded_transfer(self, transaction_id: str, funded_transfer_id: str) -> None:
        """Link transaction to funded transfer record."""
        with UnitOfWork() as uow:
            try:
                transaction = uow.transactions.get(transaction_id)
                if transaction:
                    transaction.funded_transfer_id = funded_transfer_id
                    uow.commit()
                    logger.info(
                        "transaction_linked_to_funded_transfer",
                        transaction_id=transaction_id,
                        funded_transfer_id=funded_transfer_id,
                    )
            except Exception as e:
                uow.rollback()
                logger.error("failed_to_link_funded_transfer", error=str(e), exc_info=True)


async def authorize_transaction(
    state: TransferState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService | None = None,
) -> TransferState:
    """Authorize transaction after PIN verification - node function for graph."""
    auth = TransferAuthorization(redis_client, queue, authorization_service)
    return await auth.authorize(state)
