"""Airtime confirmation step."""

from typing import Any

from banking.bills.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from banking.bills.airtime.pipeline.base import AirtimeStep
from banking.presentation.formatters.airtime import format_airtime_summary
from banking.presentation.i18n.personality import PersonalityContext
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.shared.confirmation_updates import build_airtime_confirmation_update_message
from banking.transactions.shared.scheduling import format_schedule_confirmation_line
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(AirtimeStep):
    """Generates confirmation snapshot."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        snapshot = {
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "recipient_name": data.recipient_name,
            "source_bank_name": data.source_bank_name,
            "source_account_number": data.source_account_number,
            "source_account": data.source_account_number,
        }

        if gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK)

        summary = format_airtime_summary(
            {
                "amount": data.amount,
                "recipientPhone": data.recipient_phone,
                "network": data.network,
                "recipientName": data.recipient_name,
                "isSelf": data.is_self,
                "sourceBank": data.source_bank_name,
                "sourceAccount": data.source_account_number,
            },
            locale=context.language,
            personality_context=PersonalityContext(
                moment="confirmation",
                amount=data.amount,
                saved_recipient=bool(data.beneficiary_id or data.is_self),
            ),
        )
        schedule_line = format_schedule_confirmation_line(data, context.language)
        if schedule_line:
            summary = f"{summary}\n\n{schedule_line}"

        try:
            redis_client = getattr(worker_context, "redis_client", None)
            key = data.idempotency_key

            if redis_client:
                # Persist tokens so Webhook can look them up
                await redis_client.setex(
                    f"airtime:token:{key}:phone",
                    3600,
                    context.phone_number,
                )
            else:
                logger.debug("redis_client_not_in_context_skip_airtime_token_persistence")
        except Exception as e:
            logger.error("failed_to_persist_airtime_token", error=str(e))

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=snapshot,
            update_message=build_airtime_confirmation_update_message(
                previous_snapshot=data.previous_confirmation_snapshot,
                current_snapshot=snapshot,
                locale=context.language,
            ),
        )
