"""Execution Node for Transfer Pipeline."""

from decimal import Decimal
from typing import Any

from banking.presentation.formatters.transfer_notifications import format_transfer_queued_message
from banking.presentation.i18n.renderer import render_message, render_text
from banking.risk.service import RiskDecisionService
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.funding.plan_validation import funding_adjustment_details, funding_plan_confirmability
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from banking.transfers.resolution.modes import POOLED_MODE, is_pooled_funding_plan
from shared.config.settings import settings
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.money import naira_to_json, require_naira, to_naira
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

        dd_provider = getattr(worker_context, "dd_provider", None)
        if dd_provider is not None:
            confirmable, funding_reason = funding_plan_confirmability(data)
            if not confirmable:
                logger.error(
                    "transfer_execution_rejected_invalid_funding_plan",
                    reason=funding_reason,
                    has_funding_plan=isinstance(data.funding_plan, dict),
                )
                return _funding_adjustment_result(data, locale, funding_reason)

            if is_pooled_funding_plan(data.funding_plan):
                payout_provider = settings.payout_provider_name.strip().lower()
                if (
                    data.recipient_resolution_mode != POOLED_MODE
                    or data.recipient_bank_code_provider
                    or "".strip().lower() != payout_provider
                ):
                    logger.error(
                        "transfer_execution_rejected_recipient_resolution_mode_mismatch",
                        recipient_resolution_mode=data.recipient_resolution_mode,
                        recipient_bank_code_provider=data.recipient_bank_code_provider,
                        expected_provider=payout_provider,
                    )
                    return _funding_adjustment_result(data, locale, "recipient_resolution_mode")

            still_available = await _confirmed_funding_still_available(data, context, dd_provider)
            if not still_available:
                logger.warning("transfer_execution_rejected_balance_drift")
                return _funding_adjustment_result(data, locale, "balance_drift")

        try:
            transaction_id = None
            key = data.idempotency_key
            funded_transfer_id: str | None = None

            narration = format_narration(data.narration, data.recipient_resolved_name or data.recipient_name)
            completion_metadata = _completion_service_metadata(data=data, context=context, narration=narration)

            from banking.persistence.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                try:
                    existing = await uow.transactions.get_by_idempotency_key(key)
                    if existing:
                        transaction_id = str(existing.id)
                        risk_review_released = existing.status == TransactionStatusEnum.REVIEW_PENDING.value
                        if risk_review_released:
                            existing.status = TransactionStatusEnum.PENDING.value
                            existing.provider_status = None
                            existing.error_message = None
                        existing.service_metadata = {
                            **(existing.service_metadata or {}),
                            **completion_metadata,
                        }
                        if risk_review_released:
                            existing.service_metadata["risk_advisory_released"] = True
                    else:
                        risk = await RiskDecisionService().evaluate_transfer(
                            uow=uow,
                            payload=data,
                            context=context,
                            worker_context=worker_context,
                        )
                        service_metadata = dict(completion_metadata)
                        if risk.has_concerns:
                            service_metadata["risk_advisory"] = {
                                "decision": risk.decision,
                                "reason_codes": risk.reason_codes,
                                "score": risk.score,
                                "metadata": risk.metadata,
                            }
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
                            service_metadata=service_metadata or None,
                        )
                        transaction_id = str(tx.id)
                        logger.info("transaction_persisted", id=transaction_id, key=key)

                    if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                        funded = await uow.funded_transfers.get_by_idempotency_key(key)
                        if not funded:
                            funded = await uow.funded_transfers.create(
                                user_id=getattr(worker_context, "user_id", None),
                                amount=require_naira(data.amount),
                                currency="NGN",
                                recipient_account_number=data.recipient_account or "",
                                recipient_bank_code=data.recipient_bank_code or "",
                                recipient_bank_name=data.recipient_bank_name or "",
                                recipient_name=data.recipient_resolved_name or data.recipient_name or "Recipient",
                                narration=narration,
                                payout_provider=settings.payout_provider_name,
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
                                    amount=require_naira(step.get("amount")),
                                    sequence=int(step.get("sequence", 0)),
                                    status=FundingStepStatusEnum.PENDING.value,
                                    provider_name=settings.account_provider_name,
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
                response=format_transfer_queued_message(
                    amount=data.amount or Decimal(0),
                    recipient_name=data.recipient_resolved_name or data.recipient_name or "",
                    locale=locale,
                ),
            )

        except Exception as e:
            logger.error("transfer_execution_failed", error=str(e))
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.execution_failed", locale),
                retryable=True,
            )


def _funding_adjustment_result(
    data: TransferPayload,
    locale: str,
    reason: str | None,
) -> TransactionResult:
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["funding_plan", "amount", "source_accounts", "explicit_split"],
        prompt=render_text(
            "The funding for this transfer needs to be reviewed before I can continue. "
            "You can reduce the amount or tell me which account(s) to use.",
            locale,
        ),
        details=funding_adjustment_details(reason),
        patch={**data.model_dump(exclude_none=True), "funding_plan": None},
    )


def _completion_service_metadata(
    *,
    data: TransferPayload,
    context: TransferContext,
    narration: str | None,
) -> dict[str, Any]:
    """Persist enough context for async pooled-transfer completion notifications."""
    async_group = None
    if data.async_group_id and data.async_group_size and data.async_group_kind and data.async_group_index:
        async_group = {
            "async_group_id": data.async_group_id,
            "async_group_size": data.async_group_size,
            "async_group_kind": data.async_group_kind,
            "async_group_index": data.async_group_index,
        }
    completion_context = {
        "domain": "transfer",
        "phone_number": context.phone_number,
        "channel": context.channel,
        "channel_identity": context.channel_identity,
        "language": context.language,
        "async_group": async_group,
        "amount_naira": naira_to_json(data.amount),
        "recipient_name": data.recipient_resolved_name or data.recipient_name,
        "recipient_account": data.recipient_account,
        "recipient_bank_code": data.recipient_bank_code,
        "recipient_bank_name": data.recipient_bank_name,
        "source_account_id": data.source_account_id,
        "source_account_number": data.source_account_number,
        "source_account_name": data.source_account_name,
        "source_bank_name": data.source_bank_name,
        "source_affinity_mode": data.source_affinity_mode,
        "narration": narration,
    }
    filtered_context = {key: value for key, value in completion_context.items() if value not in (None, "", [], {})}
    return {"completion_context": filtered_context}


def _account_by_id(accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(account.get("id") or account.get("account_id")): account
        for account in accounts
        if isinstance(account, dict) and (account.get("id") or account.get("account_id"))
    }


async def _confirmed_funding_still_available(
    data: TransferPayload,
    context: TransferContext,
    provider: Any,
) -> bool:
    plan = data.funding_plan if isinstance(data.funding_plan, dict) else {}
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False

    accounts = _account_by_id(context.all_accounts or context.accounts)
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            return False
        amount = to_naira(raw_step.get("amount"))
        if amount is None or amount <= 0:
            return False
        account = accounts.get(str(raw_step.get("account_id") or ""))
        if not account:
            return False
        mono_account_id = account.get("mono_account_id") or account.get("account_id")
        if not mono_account_id:
            return False
        try:
            balance = await provider.get_balance(mono_account_id, real_time=True)
        except Exception as exc:
            logger.warning("transfer_execution_balance_recheck_failed", error=str(exc))
            return False
        available = to_naira(getattr(balance, "available_balance", None))
        if not getattr(balance, "success", False) or available is None or available < amount:
            return False
    return True
