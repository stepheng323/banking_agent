"""Dispatcher for due scheduled transfer instructions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.database.enums import ScheduledInstructionStatusEnum, ScheduledRunStatusEnum, TransactionStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.scheduling.recurrence import compute_next_run_utc
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferScheduleDispatcher:
    """Dispatches due transfer schedules to the existing transaction queue."""

    def __init__(self, publisher: QueuePublisher, *, max_due_per_tick: int = 50) -> None:
        self.publisher = publisher
        self.max_due_per_tick = max_due_per_tick

    async def dispatch_due(self) -> dict[str, int]:
        """Fetch due schedules, enqueue transfer execution jobs, and advance recurrence."""
        processed = 0
        skipped = 0
        now_utc = datetime.now(UTC).replace(tzinfo=None)

        async with UnitOfWork() as uow:
            if not uow.scheduled_instructions or not uow.scheduled_runs or not uow.transactions:
                raise RuntimeError("schedule_dispatcher_missing_repositories")

            due_schedules = await uow.scheduled_instructions.get_due_active(
                now_utc=now_utc,
                limit=self.max_due_per_tick,
            )
            for schedule in due_schedules:
                payload = schedule.payload_snapshot if isinstance(schedule.payload_snapshot, dict) else {}
                amount = float(payload.get("amount") or 0.0)
                recipient_account = payload.get("recipient_account")
                recipient_bank_code = payload.get("recipient_bank_code")
                source_account_id = payload.get("source_account_id")
                if (
                    amount <= 0
                    or not recipient_account
                    or not recipient_bank_code
                    or not source_account_id
                ):
                    skipped += 1
                    logger.warning(
                        "schedule_dispatch_skipped_invalid_snapshot",
                        schedule_id=str(schedule.id),
                    )
                    continue

                due_at = schedule.next_run_at_utc or now_utc
                due_epoch = int(due_at.replace(tzinfo=UTC).timestamp())
                run_idempotency = f"sched:{schedule.id}:{due_epoch}"

                existing_run = await uow.scheduled_runs.get_by_idempotency_key(run_idempotency)
                if existing_run:
                    skipped += 1
                    continue

                scheduled_run = await uow.scheduled_runs.create(
                    schedule_id=schedule.id,
                    due_at_utc=due_at,
                    status=ScheduledRunStatusEnum.QUEUED.value,
                    attempt=1,
                    idempotency_key=run_idempotency,
                )

                tx = await uow.transactions.create(
                    idempotency_key=run_idempotency,
                    transaction_type="transfer",
                    status=TransactionStatusEnum.PENDING.value,
                    user_id=schedule.user_id,
                    amount=amount,
                    recipient_account_number=str(recipient_account),
                    recipient_bank_code=str(recipient_bank_code),
                    recipient_name=str(
                        payload.get("recipient_resolved_name") or payload.get("recipient_name") or "Recipient"
                    ),
                    recipient_bank_name=str(payload.get("recipient_bank_name") or ""),
                    source_account_id=source_account_id,
                    source_account_number=str(payload.get("source_account_number") or ""),
                    source_bank_name=str(payload.get("source_bank_name") or ""),
                    narration=payload.get("narration"),
                )
                transaction_id = str(tx.id)
                scheduled_run.transaction_id = transaction_id

                next_run = compute_next_run_utc(
                    recurrence_type=str(schedule.recurrence_type),
                    due_at_utc=due_at,
                    local_time=schedule.local_time,
                    day_of_month=schedule.day_of_month,
                    timezone=schedule.timezone,
                )
                schedule.last_run_at_utc = due_at
                if next_run is None:
                    schedule.status = ScheduledInstructionStatusEnum.COMPLETED.value
                else:
                    schedule.next_run_at_utc = next_run
                    schedule.status = ScheduledInstructionStatusEnum.ACTIVE.value

                transfer_data = {
                    "amount": amount,
                    "recipient": {
                        "account_number": payload.get("recipient_account"),
                        "bank_code": payload.get("recipient_bank_code"),
                        "bank_name": payload.get("recipient_bank_name"),
                        "name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                    },
                    "source": {
                        "account_id": payload.get("source_account_id"),
                        "account_number": payload.get("source_account_number"),
                        "bank_name": payload.get("source_bank_name"),
                        "account_name": payload.get("source_account_name"),
                    },
                    "narration": payload.get("narration"),
                }

                message: dict[str, Any] = {
                    "type": "execute_transfer",
                    "idempotency_key": run_idempotency,
                    "transaction_id": transaction_id,
                    "phone_number": str(schedule.channel_identity or ""),
                    "language": str(payload.get("language") or "en"),
                    "channel": str(schedule.channel or "whatsapp"),
                    "channel_identity": schedule.channel_identity,
                    "transfer_data": transfer_data,
                    "scheduled_meta": {
                        "schedule_id": str(schedule.id),
                        "schedule_run_id": str(scheduled_run.id),
                        "run_source": "scheduled",
                        "attempt": 1,
                        "channel": str(schedule.channel or "whatsapp"),
                        "channel_identity": schedule.channel_identity,
                    },
                }
                await self.publisher.publish(topic="transaction.execute", message=message)
                processed += 1

            await uow.commit()

        logger.info("schedule_dispatch_completed", processed=processed, skipped=skipped)
        return {"processed": processed, "skipped": skipped}
