"""Scheduled transaction run helpers for runtime executors."""

from datetime import UTC, datetime
from typing import Any

from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_TERMINAL_RUN_STATUSES = {"successful", "failed"}


def scheduled_meta(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("scheduled_meta")
    return raw if isinstance(raw, dict) else {}


def schedule_run_id(meta: dict[str, Any]) -> str | None:
    value = meta.get("schedule_run_id")
    return str(value) if value else None


def is_scheduled_run(meta: dict[str, Any]) -> bool:
    return str(meta.get("run_source") or "") == "scheduled"


async def update_scheduled_run(
    schedule_run_id: str | None,
    *,
    status: str,
    error_message: str | None = None,
    transaction_id: str | None = None,
    attempt: int | None = None,
    warning_event: str = "scheduled_run_update_failed",
) -> None:
    if not schedule_run_id:
        return
    try:
        async with UnitOfWork() as uow:
            if not uow.scheduled_runs:
                return
            run = await uow.scheduled_runs.get_by_id(schedule_run_id)
            if not run:
                return
            run.status = status
            run.error_message = error_message
            run.transaction_id = transaction_id or run.transaction_id
            if attempt is not None:
                run.attempt = attempt
            if status in _TERMINAL_RUN_STATUSES:
                run.completed_at = datetime.now(UTC).replace(tzinfo=None)
            uow.db.add(run)
            await uow.commit()
    except Exception as exc:
        logger.warning(warning_event, schedule_run_id=schedule_run_id, error=str(exc))
