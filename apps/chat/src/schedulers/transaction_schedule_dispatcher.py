"""Dispatcher for due scheduled transaction instructions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.policy.service import capability_block_message
from banking.scheduling.services.recurrence import compute_next_run_utc
from shared.database.enums import ScheduledInstructionStatusEnum, ScheduledRunStatusEnum, TransactionStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionScheduleDispatcher:
    """Dispatches due transaction schedules to the existing transaction queue."""

    def __init__(self, publisher: QueuePublisher, *, max_due_per_tick: int = 50) -> None:
        self.publisher = publisher
        self.max_due_per_tick = max_due_per_tick

    async def dispatch_due(self) -> dict[str, int]:
        """Fetch due schedules, enqueue transaction execution jobs, and advance recurrence."""
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
                domain = str(getattr(schedule, "domain", None) or payload.get("domain") or "transfer")
                locale = str(payload.get("language") or "en")
                schedule_action = {
                    "transfer": "schedule_transfer",
                    "airtime": "schedule_airtime",
                    "data": "schedule_data",
                }.get(domain)
                domain_action = {
                    "transfer": "send_money",
                    "airtime": "buy_airtime",
                    "data": "buy_data",
                }.get(domain)
                if not schedule_action or not domain_action:
                    skipped += 1
                    logger.warning(
                        "schedule_dispatch_skipped_unknown_domain",
                        schedule_id=str(schedule.id),
                        domain=domain,
                    )
                    continue
                policy_block_message = capability_block_message(
                    domain="schedule",
                    action=schedule_action,
                    locale=locale,
                ) or capability_block_message(
                    domain=domain,
                    action=domain_action,
                    locale=locale,
                )
                if policy_block_message:
                    skipped += 1
                    logger.info(
                        "schedule_dispatch_skipped_policy_blocked",
                        schedule_id=str(schedule.id),
                    )
                    continue

                source_account_id = payload.get("source_account_id")
                amount = float(payload.get("amount") or 0.0)
                if not self._is_valid_snapshot(domain=domain, payload=payload, amount=amount):
                    skipped += 1
                    logger.warning(
                        "schedule_dispatch_skipped_invalid_snapshot",
                        schedule_id=str(schedule.id),
                        domain=domain,
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
                    transaction_type=domain,
                    status=TransactionStatusEnum.PENDING.value,
                    user_id=schedule.user_id,
                    amount=amount,
                    recipient_account_number=self._transfer_recipient_account_number(domain=domain, payload=payload),
                    recipient_bank_code=self._transfer_recipient_code(domain=domain, payload=payload),
                    recipient_name=self._transfer_recipient_name(domain=domain, payload=payload),
                    recipient_bank_name=self._transfer_recipient_bank_name(domain=domain, payload=payload),
                    target_phone_number=self._target_phone_number(domain=domain, payload=payload),
                    mobile_network=self._mobile_network(domain=domain, payload=payload),
                    biller_code=self._data_biller_value(domain=domain, payload=payload, key="biller_code"),
                    biller_item_code=self._data_biller_value(domain=domain, payload=payload, key="plan_code"),
                    biller_item_name=self._data_biller_value(domain=domain, payload=payload, key="plan_name"),
                    service_metadata=self._service_metadata(domain=domain, payload=payload),
                    source_account_id=source_account_id,
                    source_account_number=str(payload.get("source_account_number") or ""),
                    source_bank_name=str(payload.get("source_bank_name") or ""),
                    narration=self._narration(domain=domain, payload=payload),
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

                scheduled_meta = {
                    "schedule_id": str(schedule.id),
                    "schedule_run_id": str(scheduled_run.id),
                    "run_source": "scheduled",
                    "attempt": 1,
                    "channel": str(schedule.channel or "whatsapp"),
                    "channel_identity": schedule.channel_identity,
                }
                message: dict[str, Any] = {
                    "idempotency_key": run_idempotency,
                    "transaction_id": transaction_id,
                    "phone_number": str(schedule.channel_identity or ""),
                    "language": locale,
                    "channel": str(schedule.channel or "whatsapp"),
                    "channel_identity": schedule.channel_identity,
                    "scheduled_meta": scheduled_meta,
                }
                message.update(self._execution_payload(domain=domain, payload=payload, amount=amount))
                await self.publisher.publish(topic="transaction.execute", message=message)
                processed += 1

            await uow.commit()

        logger.info("schedule_dispatch_completed", processed=processed, skipped=skipped)
        return {"processed": processed, "skipped": skipped}

    @staticmethod
    def _is_valid_snapshot(*, domain: str, payload: dict[str, Any], amount: float) -> bool:
        if amount <= 0 or not payload.get("source_account_id"):
            return False
        if domain == "transfer":
            return bool(payload.get("recipient_account") and payload.get("recipient_bank_code"))
        if domain == "airtime":
            return bool(payload.get("recipient_phone") and payload.get("network"))
        if domain == "data":
            return bool(payload.get("target_phone") and payload.get("network"))
        return False

    @staticmethod
    def _transfer_recipient_account_number(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain == "transfer":
            return str(payload.get("recipient_account") or "") or None
        return None

    @staticmethod
    def _transfer_recipient_code(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain == "transfer":
            return str(payload.get("recipient_bank_code") or "") or None
        return None

    @staticmethod
    def _transfer_recipient_name(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain == "transfer":
            return str(payload.get("recipient_resolved_name") or payload.get("recipient_name") or "Recipient")
        return None

    @staticmethod
    def _transfer_recipient_bank_name(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain == "transfer":
            return str(payload.get("recipient_bank_name") or "") or None
        return None

    @staticmethod
    def _target_phone_number(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain == "airtime":
            return str(payload.get("recipient_phone") or "") or None
        if domain == "data":
            return str(payload.get("target_phone") or "") or None
        return None

    @staticmethod
    def _mobile_network(*, domain: str, payload: dict[str, Any]) -> str | None:
        if domain in {"airtime", "data"}:
            return str(payload.get("network") or "") or None
        return None

    @staticmethod
    def _data_biller_value(*, domain: str, payload: dict[str, Any], key: str) -> str | None:
        if domain != "data":
            return None
        return str(payload.get(key) or "") or None

    @staticmethod
    def _service_metadata(*, domain: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if domain == "airtime":
            metadata = {
                "recipient_name": payload.get("recipient_name"),
                "beneficiary_id": payload.get("beneficiary_id"),
                "is_self": payload.get("is_self"),
            }
        elif domain == "data":
            metadata = {
                "size_gb": payload.get("plan_size_gb"),
                "validity_days": payload.get("plan_validity_days"),
                "tags": payload.get("plan_tags"),
                "is_self": payload.get("is_self"),
            }
        else:
            metadata = {}
        compact: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, bool):
                if value:
                    compact[key] = value
                continue
            if value not in (None, "", [], {}):
                compact[key] = value
        return compact or None

    @staticmethod
    def _narration(*, domain: str, payload: dict[str, Any]) -> str | None:
        if payload.get("narration"):
            return str(payload.get("narration"))
        if domain == "airtime":
            return f"Airtime: {payload.get('recipient_phone') or ''} ({payload.get('network') or ''})"
        if domain == "data":
            if payload.get("plan_name"):
                return f"Data: {payload.get('plan_name')} for {payload.get('target_phone') or ''}"
            return f"Data for {payload.get('target_phone') or ''}"
        return None

    @staticmethod
    def _execution_payload(*, domain: str, payload: dict[str, Any], amount: float) -> dict[str, Any]:
        if domain == "transfer":
            return {
                "type": "execute_transfer",
                "transfer_data": {
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
                },
            }
        if domain == "airtime":
            return {
                "type": "execute_airtime",
                "airtime_data": {
                    "amount": amount,
                    "phone_number": payload.get("recipient_phone"),
                    "network": payload.get("network"),
                    "source_account_number": payload.get("source_account_number"),
                    "source_account_id": payload.get("source_account_id"),
                    "source_bank_name": payload.get("source_bank_name"),
                    "source_affinity_mode": payload.get("source_affinity_mode"),
                },
            }
        return {
            "type": "execute_data",
            "data_purchase": {
                "plan_code": payload.get("plan_code"),
                "plan_name": payload.get("plan_name"),
                "biller_code": payload.get("biller_code"),
                "amount": amount,
                "target_phone": payload.get("target_phone"),
                "network": payload.get("network"),
                "source": payload.get("source_account_number") or "",
                "source_account_id": payload.get("source_account_id"),
                "source_account_number": payload.get("source_account_number"),
                "source_bank_name": payload.get("source_bank_name"),
                "source_affinity_mode": payload.get("source_affinity_mode"),
                "is_self": payload.get("is_self"),
            },
        }
