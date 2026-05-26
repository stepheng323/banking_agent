from typing import Any

from apps.chat.src.agent.graphs.__shared__.confirmation_updates import build_data_confirmation_update_message
from apps.chat.src.agent.graphs.__shared__.scheduling import format_schedule_confirmation_line
from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.cache.redis_client import RedisClient
from shared.formatters.data import format_data_summary
from shared.i18n import render_message
from shared.i18n.personality import PersonalityContext
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(PipelineStep):
    """Confirmation Step: Generate confirmation snapshot."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if gates.confirmation_confirmed:
            return None

        locale = context.language
        if not payload.plan_code or not payload.plan_name or payload.amount is None:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"],
                prompt=render_message("data.plan_selection.missing_plan", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        summary = format_data_summary(
            {
                "planName": payload.plan_name,
                "amount": payload.amount,
                "recipientPhone": payload.target_phone,
                "network": payload.network,
                "sourceBank": payload.source_bank_name,
                "sourceAccount": payload.source_account_number,
                "isSelf": payload.is_self,
            },
            locale=locale,
            personality_context=PersonalityContext(
                moment="confirmation",
                amount=payload.amount,
                saved_recipient=bool(payload.beneficiary_id or payload.is_self),
            ),
        )
        schedule_line = format_schedule_confirmation_line(payload, locale)
        if schedule_line:
            summary = f"{summary}\n\n{schedule_line}"

        await _persist_data_confirmation_token(payload, context, worker_context)
        snapshot = _confirmation_snapshot(payload)

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=snapshot,
            update_message=build_data_confirmation_update_message(
                previous_snapshot=payload.previous_confirmation_snapshot,
                current_snapshot=snapshot,
                locale=locale,
            ),
            patch=payload.model_dump(exclude_none=True),
        )


def _confirmation_snapshot(payload: DataPayload) -> dict[str, Any]:
    return {
        "amount": payload.amount,
        "network": payload.network,
        "target_phone": payload.target_phone,
        "plan_code": payload.plan_code,
        "plan_name": payload.plan_name,
        "biller_code": payload.biller_code,
        "plan_size_gb": payload.plan_size_gb,
        "plan_validity_days": payload.plan_validity_days,
        "source_bank_name": payload.source_bank_name,
        "source_account_number": payload.source_account_number,
        "is_self": payload.is_self,
    }


async def _persist_data_confirmation_token(
    payload: DataPayload,
    context: DataContext,
    worker_context: Any,
) -> None:
    key = payload.idempotency_key
    if not key:
        logger.warning("data_confirmation_token_missing")
        return

    try:
        redis_client = getattr(worker_context, "redis_client", None)
        if redis_client is None:
            redis_client = RedisClient.get_client()

        if redis_client:
            await redis_client.setex(f"data:token:{key}:phone", 3600, context.phone_number)
            await redis_client.setex(f"transaction:token:{key}:phone", 3600, context.phone_number)
        else:
            logger.warning("redis_client_not_in_context_cannot_persist_data_token")
    except Exception as exc:
        logger.error("failed_to_persist_data_token", error=str(exc))
