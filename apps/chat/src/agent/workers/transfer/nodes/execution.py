"""Execution Node for Transfer Pipeline."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.workers.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.workers.transfer.pipeline.base import TransferStep
from banking.presentation.i18n.renderer import render_message
from banking.risk.service import RiskDecisionService
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.money import naira_to_json
from shared.queue.factory import QueuePublisherFactory
from shared.utils.logging import get_logger
from shared.utils.narration import format_narration

logger = get_logger(__name__)


class ExecutionStep(TransferStep):
    """Executes the transfer and creates transaction record."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        locale = context.language
        if not gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        if not gates.pin_verified:
            logger.warning("transfer_execution_rejected_unauthorized")
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_AUTH,
                patch=data.model_dump(exclude_none=True),
            )

        try:
            transaction_id = None
            key = data.idempotency_key
            funded_transfer_id: str | None = None

            narration = format_narration(data.narration, data.recipient_resolved_name or data.recipient_name)

            from banking.persistence.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                try:
                    existing = await uow.transactions.get_by_idempotency_key(key)
                    if existing:
                        transaction_id = str(existing.id)
                        if existing.status == TransactionStatusEnum.REVIEW_PENDING.value:
                            return TransactionResult(
                                outcome=TransactionOutcome.FAILED,
                                response="This transfer is under review before any debit is made.",
                                error="Transfer is pending risk review.",
                            )
                    else:
                        risk = await RiskDecisionService().evaluate_transfer(
                            uow=uow,
                            payload=data,
                            context=context,
                            worker_context=worker_context,
                        )
                        if risk.decision in {"hold_review", "deny"}:
                            status = (
                                TransactionStatusEnum.REVIEW_PENDING.value
                                if risk.decision == "hold_review"
                                else TransactionStatusEnum.FAILED.value
                            )
                            tx = await uow.transactions.create(
                                idempotency_key=key,
                                transaction_type="transfer",
                                status=status,
                                user_id=getattr(worker_context, "user_id", None),
                                amount=data.amount,
                                recipient_account_number=data.recipient_account,
                                recipient_bank_code=data.recipient_bank_code,
                                recipient_name=data.recipient_resolved_name or data.recipient_name or "",
                                recipient_bank_name=data.recipient_bank_name or "",
                                source_account_id=data.source_account_id,
                                source_account_number=data.source_account_number or "",
                                source_bank_name=data.source_bank_name or "",
                                narration=narration,
                                provider_status="risk_review" if risk.decision == "hold_review" else "risk_denied",
                                provider_response={
                                    "decision": risk.decision,
                                    "reason_codes": risk.reason_codes,
                                    "score": risk.score,
                                    "metadata": risk.metadata,
                                },
                                error_message=(
                                    "Transfer held for risk review before debit"
                                    if risk.decision == "hold_review"
                                    else "Transfer denied by risk policy"
                                ),
                            )
                            transaction_id = str(tx.id)
                            if risk.decision == "hold_review":
                                if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                                    funded = await uow.funded_transfers.get_by_idempotency_key(key)
                                    if not funded:
                                        funded = await uow.funded_transfers.create(
                                            user_id=getattr(worker_context, "user_id", None),
                                            amount=float(data.amount or 0.0),
                                            currency="NGN",
                                            recipient_account_number=data.recipient_account or "",
                                            recipient_bank_code=data.recipient_bank_code or "",
                                            recipient_bank_name=data.recipient_bank_name or "",
                                            recipient_name=data.recipient_resolved_name or data.recipient_name or "Recipient",
                                            narration=narration,
                                            payout_provider="flutterwave",
                                            status=FundedTransferStatusEnum.REVIEW_PENDING.value,
                                            idempotency_key=key,
                                            error_message="Transfer held for risk review before funding",
                                        )
                                    funded_transfer_id = str(funded.id)
                                await RiskDecisionService().create_review_ticket(
                                    uow=uow,
                                    user_id=str(getattr(worker_context, "user_id", "") or ""),
                                    idempotency_key=str(key),
                                    decision=risk,
                                    channel=context.channel,
                                )
                            await uow.commit()
                            message = (
                                "This transfer is under review before any debit is made."
                                if risk.decision == "hold_review"
                                else "This transfer cannot be processed."
                            )
                            return TransactionResult(
                                outcome=TransactionOutcome.FAILED,
                                response=message,
                                error=message,
                                patch={"transaction_id": transaction_id} if transaction_id else {},
                            )

                        tx = await uow.transactions.create(
                            idempotency_key=key,
                            transaction_type="transfer",
                            status=TransactionStatusEnum.PENDING.value,
                            user_id=getattr(worker_context, "user_id", None),
                            amount=data.amount,
                            recipient_account_number=data.recipient_account,
                            recipient_bank_code=data.recipient_bank_code,
                            recipient_name=data.recipient_resolved_name or data.recipient_name or "",
                            recipient_bank_name=data.recipient_bank_name or "",
                            source_account_id=data.source_account_id,
                            source_account_number=data.source_account_number or "",
                            source_bank_name=data.source_bank_name or "",
                            narration=narration,
                        )
                        transaction_id = str(tx.id)
                        logger.info("transaction_persisted", id=transaction_id, key=key)

                    if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                        funded = await uow.funded_transfers.get_by_idempotency_key(key)
                        if not funded:
                            funded = await uow.funded_transfers.create(
                                user_id=getattr(worker_context, "user_id", None),
                                amount=float(data.amount or 0.0),
                                currency="NGN",
                                recipient_account_number=data.recipient_account or "",
                                recipient_bank_code=data.recipient_bank_code or "",
                                recipient_bank_name=data.recipient_bank_name or "",
                                recipient_name=data.recipient_resolved_name or data.recipient_name or "Recipient",
                                narration=narration,
                                payout_provider="flutterwave",
                                status=FundedTransferStatusEnum.FUNDING_PENDING.value,
                                idempotency_key=key,
                            )
                        funded_transfer_id = str(funded.id)

                        existing_steps = await uow.funding_steps.get_by_transfer(funded_transfer_id)
                        if not existing_steps:
                            for step in data.funding_plan.get("steps", []):
                                await uow.funding_steps.create(
                                    funded_transfer_id=funded.id,
                                    account_id=step.get("account_id"),
                                    amount=float(step.get("amount", 0.0)),
                                    sequence=int(step.get("sequence", 0)),
                                    status=FundingStepStatusEnum.PENDING.value,
                                    provider_name="mono",
                                )
                    await uow.commit()
                except Exception as e:
                    logger.error("failed_to_persist_transaction", error=str(e))
                    await uow.rollback()
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message("transfer.error.execution_failed", locale),
                        retryable=True,
                    )

            publisher = getattr(worker_context, "publisher", None)
            if not publisher:
                publisher = QueuePublisherFactory.get_async_publisher()

            if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                if not funded_transfer_id:
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message(
                            "transfer.execution.failed",
                            locale,
                            {"error": render_message("transfer.execution.funding_setup_failed", locale)},
                        ),
                        retryable=True,
                    )
                await publisher.publish(
                    topic="funding.process",
                    message={
                        "type": "initiate_funding",
                        "funded_transfer_id": funded_transfer_id,
                        "idempotency_key": key,
                        "transaction_id": transaction_id,
                        "narration": narration,
                    },
                )
            else:
                amount_naira = naira_to_json(data.amount) or "0.00"
                async_group = None
                if data.async_group_id and data.async_group_size and data.async_group_kind and data.async_group_index:
                    async_group = {
                        "async_group_id": data.async_group_id,
                        "async_group_size": data.async_group_size,
                        "async_group_kind": data.async_group_kind,
                        "async_group_index": data.async_group_index,
                    }
                await publisher.publish(
                    topic="transaction.execute",
                    message={
                        "type": "execute_transfer",
                        "idempotency_key": key,
                        "transaction_id": transaction_id,
                        "phone_number": context.phone_number,
                        "channel": context.channel,
                        "channel_identity": context.channel_identity,
                        "language": locale,
                        "transfer_data": {
                            "amount": amount_naira,
                            "amount_naira": amount_naira,
                            "recipient": {
                                "account_number": data.recipient_account,
                                "bank_code": data.recipient_bank_code,
                                "bank_code_provider": data.recipient_bank_code_provider,
                                "resolution_provider": data.recipient_resolution_provider,
                                "name": data.recipient_resolved_name or data.recipient_name,
                                "bank_name": data.recipient_bank_name,
                            },
                            "source": {
                                "account_id": data.source_account_id,
                                "account_number": data.source_account_number,
                                "account_name": data.source_account_name,
                                "bank_name": data.source_bank_name,
                            },
                            "narration": narration,
                            "source_affinity_mode": data.source_affinity_mode,
                            "beneficiary_id": data.beneficiary_id,
                            "resolved_from_saved_beneficiary": data.resolved_from_saved_beneficiary,
                            "is_high_risk_transfer": data.is_high_risk_transfer,
                            "dynamic_risk_threshold": data.dynamic_risk_threshold,
                            "funding_plan": data.funding_plan,
                        },
                        "async_group": async_group,
                    },
                )

            receipt_data = {
                "status": "processing",
                "id": key,
                "amount": data.amount,
                "recipient_account": data.recipient_account,
                "recipient_bank_code": data.recipient_bank_code,
                "recipient_name": data.recipient_resolved_name or data.recipient_name,
                "narration": narration,
                "date": render_message("transfer.execution.date_now", locale),
            }
            if data.source_bank_name:
                receipt_data["source_bank"] = data.source_bank_name

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt=receipt_data,
                patch={"transaction_id": transaction_id} if transaction_id else {},
            )

        except Exception as e:
            logger.error("transfer_execution_failed", error=str(e))
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.execution_failed", locale),
                retryable=True,
            )
