"""Shared scheduled transaction management helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from apps.chat.src.agent.graphs.__shared__.scheduling import format_schedule_confirmation_line
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.formatters.currency import format_naira
from shared.services.scheduling.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    normalize_time_local,
    today_lagos,
)

_DOMAIN_LABELS = {
    "transfer": "Transfer",
    "airtime": "Airtime",
    "data": "Data",
}
_SCHEDULE_FIELDS = {
    "schedule_mode",
    "recurrence_type",
    "schedule_timezone",
    "schedule_start_date",
    "schedule_time_local",
    "schedule_day_of_week",
    "schedule_day_of_month",
    "schedule_end_date",
}
_SCHEDULE_AUTH_FIELDS = {
    "schedule_mode",
    "recurrence_type",
}
_COMMON_EDIT_FIELDS = {
    "amount",
    "source_account_id",
    "source_bank_name",
    "source_account_name",
    "source_account_number",
    "source_account_index",
}
_DOMAIN_EDIT_FIELDS = {
    "transfer": {
        "recipient_name",
        "recipient_resolved_name",
        "recipient_account",
        "recipient_bank_code",
        "recipient_bank_name",
        "narration",
    },
    "airtime": {
        "recipient_phone",
        "network",
    },
    "data": {
        "target_phone",
        "recipient_phone",
        "network",
        "plan_code",
        "plan_name",
    },
}
_COMMON_AUTH_FIELDS = {
    "amount",
    "source_account_id",
    "source_bank_name",
    "source_account_name",
    "source_account_number",
    "source_account_index",
}
_DOMAIN_AUTH_FIELDS = {
    "transfer": {
        "recipient_name",
        "recipient_resolved_name",
        "recipient_account",
        "recipient_bank_code",
        "recipient_bank_name",
    },
    "airtime": {
        "recipient_phone",
        "network",
    },
    "data": {
        "target_phone",
        "recipient_phone",
        "network",
        "plan_code",
        "plan_name",
    },
}


@dataclass(slots=True)
class ScheduleSelection:
    schedules: list[Any]
    selected: Any | None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _digits_only(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\D+", "", str(value))


def _payload(schedule: Any) -> dict[str, Any]:
    snapshot = getattr(schedule, "payload_snapshot", None)
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _domain(schedule: Any) -> str:
    payload = _payload(schedule)
    return str(getattr(schedule, "domain", None) or payload.get("domain") or "transfer").strip().lower()


def _recurrence_label(schedule: Any) -> str:
    recurrence = str(getattr(schedule, "recurrence_type", None) or "one_time")
    return recurrence.replace("_", " ").title()


def _time_label(schedule: Any) -> str:
    local_time = str(getattr(schedule, "local_time", "") or _payload(schedule).get("schedule_time_local") or "")
    normalized = normalize_time_local(local_time) or local_time
    try:
        return datetime.strptime(normalized, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return normalized or "time not set"


def _time_value_label(value: Any) -> str:
    normalized = normalize_time_local(str(value or "")) or str(value or "")
    try:
        return datetime.strptime(normalized, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return normalized or "not set"


def _target_label(schedule: Any) -> str:
    payload = _payload(schedule)
    domain = _domain(schedule)
    if domain == "airtime":
        phone = payload.get("recipient_phone") or payload.get("phone") or "recipient"
        network = payload.get("network")
        return f"{network} airtime for {phone}" if network else f"airtime for {phone}"
    if domain == "data":
        phone = payload.get("target_phone") or payload.get("recipient_phone") or "recipient"
        plan = payload.get("plan_name") or payload.get("plan") or "data"
        network = payload.get("network")
        return f"{plan} {network} data for {phone}" if network else f"{plan} for {phone}"
    return (
        str(payload.get("recipient_resolved_name") or payload.get("recipient_name") or "").strip()
        or str(payload.get("recipient_account") or "").strip()
        or "recipient"
    )


def _amount_label(schedule: Any) -> str:
    payload = _payload(schedule)
    amount = payload.get("amount")
    if isinstance(amount, (int, float)) and float(amount) > 0:
        return format_naira(float(amount))
    return "Scheduled"


def format_schedule_row(index: int, schedule: Any, *, include_id: bool = False) -> str:
    domain = _DOMAIN_LABELS.get(_domain(schedule), _domain(schedule).title())
    line = (
        f"{index}. {domain}: {_amount_label(schedule)} {_target_label(schedule)} "
        f"• {_recurrence_label(schedule)} at {_time_label(schedule)} WAT"
    )
    if include_id:
        line = f"{line} (ID: {schedule.id})"
    return line


def build_schedule_context_items(schedules: list[Any]) -> list[dict[str, Any]]:
    """Build serializable context-frame rows for scheduled transaction follow-ups."""
    items: list[dict[str, Any]] = []
    for index, schedule in enumerate(schedules, start=1):
        payload = _payload(schedule)
        amount_raw = payload.get("amount")
        amount = format_naira(float(amount_raw)) if isinstance(amount_raw, (int, float)) and amount_raw > 0 else None
        next_run_at_utc = getattr(schedule, "next_run_at_utc", None)
        next_run = (
            format_lagos_schedule_datetime(next_run_at_utc)
            if isinstance(next_run_at_utc, datetime)
            else None
        )
        row = format_schedule_row(index, schedule, include_id=False)
        label = row.split(". ", 1)[1] if row.startswith(f"{index}. ") else row
        data: dict[str, Any] = {
            "type": "scheduled_transaction",
            "schedule_id": str(getattr(schedule, "id", "")),
            "domain": _DOMAIN_LABELS.get(_domain(schedule), _domain(schedule).title()),
            "domain_key": _domain(schedule),
            "amount": amount,
            "amount_value": float(amount_raw) if isinstance(amount_raw, (int, float)) else None,
            "target": _target_label(schedule),
            "recurrence": _recurrence_label(schedule),
            "schedule_time": f"{_time_label(schedule)} WAT",
            "next_run": next_run,
            "status": str(getattr(schedule, "status", "") or "active"),
            "source_bank_name": payload.get("source_bank_name"),
            "recipient_name": payload.get("recipient_name") or payload.get("recipient_resolved_name"),
            "recipient_resolved_name": payload.get("recipient_resolved_name"),
            "recipient_account": payload.get("recipient_account"),
            "bank_name": payload.get("recipient_bank_name"),
            "recipient_phone": payload.get("recipient_phone"),
            "target_phone": payload.get("target_phone"),
            "network": payload.get("network"),
            "plan_name": payload.get("plan_name"),
            "summary": label,
        }
        items.append(
            {
                "entity_id": str(getattr(schedule, "id", index)),
                "label": label,
                "data": {key: value for key, value in data.items() if value is not None and value != ""},
            }
        )
    return items


def _schedule_search_blob(schedule: Any) -> str:
    payload = _payload(schedule)
    fields = [
        getattr(schedule, "id", None),
        _domain(schedule),
        getattr(schedule, "recurrence_type", None),
        getattr(schedule, "start_date", None),
        getattr(schedule, "local_time", None),
        payload.get("recipient_name"),
        payload.get("recipient_resolved_name"),
        payload.get("recipient_account"),
        payload.get("recipient_bank_name"),
        payload.get("recipient_phone"),
        payload.get("target_phone"),
        payload.get("network"),
        payload.get("plan_name"),
        payload.get("source_bank_name"),
    ]
    return _normalize_text(" ".join(str(field) for field in fields if field))


def _matches_filter(schedule: Any, data: Any, user_message: str | None) -> bool:
    payload = _payload(schedule)
    domain = _domain(schedule)
    text = _normalize_text(user_message)
    blob = _schedule_search_blob(schedule)

    requested_domain = None
    if re.search(r"\bairtime\b", text):
        requested_domain = "airtime"
    elif re.search(r"\bdata\b", text):
        requested_domain = "data"
    elif re.search(r"\btransfer|send|payment\b", text):
        requested_domain = "transfer"
    if requested_domain and domain != requested_domain:
        return False

    for field in ("recipient_name", "recipient_resolved_name", "recipient_bank_name", "network", "plan_name"):
        value = getattr(data, field, None)
        if isinstance(value, str) and value.strip() and _normalize_text(value) not in blob:
            return False

    for field in ("recipient_account", "recipient_phone", "target_phone"):
        value = getattr(data, field, None)
        digits = _digits_only(value)
        if digits and digits not in _digits_only(" ".join(str(v) for v in payload.values())):
            return False

    schedule_start_date = getattr(data, "schedule_start_date", None)
    if schedule_start_date:
        if str(schedule_start_date) != str(getattr(schedule, "start_date", "") or ""):
            return False
    schedule_time_local = getattr(data, "schedule_time_local", None)
    if schedule_time_local and not re.search(r"\b(?:change|edit|update|move|shift|reschedule)\b", text):
        if str(schedule_time_local) != str(getattr(schedule, "local_time", "") or ""):
            return False

    return True


def resolve_schedule_selection(
    schedules: list[Any],
    *,
    data: Any,
    user_message: str | None,
) -> ScheduleSelection:
    selector = str(getattr(data, "schedule_selector", None) or getattr(data, "schedule_id", None) or "").strip()
    if selector:
        if selector.isdigit():
            index = int(selector)
            if 1 <= index <= len(schedules):
                return ScheduleSelection(schedules=schedules, selected=schedules[index - 1])
        selected = next((schedule for schedule in schedules if str(schedule.id) == selector), None)
        return ScheduleSelection(schedules=[selected] if selected is not None else [], selected=selected)

    filtered = [schedule for schedule in schedules if _matches_filter(schedule, data, user_message)]
    if len(filtered) == 1:
        return ScheduleSelection(schedules=filtered, selected=filtered[0])
    return ScheduleSelection(schedules=filtered or schedules, selected=None)


def build_schedule_edit_patch(data: Any, *, domain: str) -> dict[str, Any]:
    allowed = _COMMON_EDIT_FIELDS | _SCHEDULE_FIELDS | _DOMAIN_EDIT_FIELDS.get(domain, set())
    patch: dict[str, Any] = {}
    for field in allowed:
        value = getattr(data, field, None)
        if value is not None:
            patch[field] = value
    if "recipient_phone" in patch and domain == "data" and "target_phone" not in patch:
        patch["target_phone"] = patch.pop("recipient_phone")
    if "schedule_timezone" not in patch and any(field in patch for field in _SCHEDULE_FIELDS):
        patch["schedule_timezone"] = SCHEDULE_TIMEZONE
    return patch


def schedule_edit_requires_auth(domain: str, edit_patch: dict[str, Any]) -> bool:
    """Return whether a schedule edit changes money movement risk."""
    normalized_domain = str(domain or "transfer").strip().lower()
    material_fields = _COMMON_AUTH_FIELDS | _SCHEDULE_AUTH_FIELDS | _DOMAIN_AUTH_FIELDS.get(normalized_domain, set())
    return any(field in edit_patch and edit_patch[field] is not None for field in material_fields)


def _merged_schedule_state(schedule: Any, edit_patch: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(schedule)
    merged = {**payload, **edit_patch}
    return {
        "recurrence_type": str(edit_patch.get("recurrence_type") or getattr(schedule, "recurrence_type", None) or "one_time"),
        "start_date": str(edit_patch.get("schedule_start_date") or getattr(schedule, "start_date", None) or today_lagos().isoformat()),
        "local_time": str(edit_patch.get("schedule_time_local") or getattr(schedule, "local_time", None) or ""),
        "timezone": str(edit_patch.get("schedule_timezone") or getattr(schedule, "timezone", None) or SCHEDULE_TIMEZONE),
        "day_of_week": edit_patch.get("schedule_day_of_week", getattr(schedule, "day_of_week", None)),
        "day_of_month": edit_patch.get("schedule_day_of_month", getattr(schedule, "day_of_month", None)),
        "end_date": edit_patch.get("schedule_end_date", getattr(schedule, "end_date", None)),
        "payload": merged,
    }


def build_schedule_update_summary(schedule: Any, edit_patch: dict[str, Any]) -> tuple[str, dict[str, Any], datetime | None]:
    merged = _merged_schedule_state(schedule, edit_patch)
    next_run_at = compute_initial_next_run_utc(
        recurrence_type=merged["recurrence_type"],
        start_date=merged["start_date"],
        local_time=merged["local_time"],
        day_of_week=merged["day_of_week"],
        day_of_month=merged["day_of_month"],
        timezone=merged["timezone"],
    )
    confirmation_data = SimpleNamespace(
        schedule_start_date=merged["start_date"],
        schedule_time_local=merged["local_time"],
        recurrence_type=merged["recurrence_type"],
    )
    schedule_line = format_schedule_confirmation_line(confirmation_data) or "Scheduled time pending"
    payload = merged["payload"]
    preview = SimpleNamespace(
        id=getattr(schedule, "id", None),
        domain=_domain(schedule),
        recurrence_type=merged["recurrence_type"],
        local_time=merged["local_time"],
        start_date=merged["start_date"],
        payload_snapshot=payload,
    )
    summary = "\n".join(
        [
            format_schedule_row(1, preview, include_id=False).removeprefix("1. "),
            schedule_line,
        ]
    )
    snapshot = {
        "schedule_id": str(schedule.id),
        "domain": _domain(schedule),
        "edit_patch": edit_patch,
        "next_run_at_utc": next_run_at.isoformat() if next_run_at else None,
    }
    return summary, snapshot, next_run_at


def build_schedule_edit_success_message(schedule: Any, edit_patch: dict[str, Any], next_run_at: datetime) -> str:
    """Render a concise success message that states what changed."""
    payload = _payload(schedule)
    changes: list[str] = []

    if "amount" in edit_patch:
        old_amount = payload.get("amount")
        new_amount = edit_patch.get("amount")
        if isinstance(old_amount, (int, float)) and isinstance(new_amount, (int, float)):
            changes.append(f"amount changed from {format_naira(float(old_amount))} to {format_naira(float(new_amount))}")
        elif new_amount is not None:
            changes.append(f"amount changed to {new_amount}")

    if "schedule_time_local" in edit_patch:
        old_time = getattr(schedule, "local_time", None)
        new_time = edit_patch.get("schedule_time_local")
        if str(old_time or "") != str(new_time or ""):
            changes.append(f"time changed from {_time_value_label(old_time)} to {_time_value_label(new_time)} WAT")

    if "schedule_start_date" in edit_patch:
        old_date = getattr(schedule, "start_date", None)
        new_date = edit_patch.get("schedule_start_date")
        if str(old_date or "") != str(new_date or ""):
            changes.append(f"date changed from {old_date or 'not set'} to {new_date}")

    if "recurrence_type" in edit_patch:
        old_recurrence = str(getattr(schedule, "recurrence_type", None) or "one_time").replace("_", " ").title()
        new_recurrence = str(edit_patch.get("recurrence_type") or "one_time").replace("_", " ").title()
        if old_recurrence != new_recurrence:
            changes.append(f"recurrence changed from {old_recurrence} to {new_recurrence}")

    target_fields = (
        "recipient_name",
        "recipient_resolved_name",
        "recipient_account",
        "recipient_bank_name",
        "recipient_phone",
        "target_phone",
        "network",
        "plan_name",
        "plan_code",
    )
    if any(field in edit_patch for field in target_fields):
        changes.append("target details updated")

    source_fields = (
        "source_account_id",
        "source_bank_name",
        "source_account_name",
        "source_account_number",
        "source_account_index",
    )
    if any(field in edit_patch for field in source_fields):
        changes.append("source account updated")

    if "narration" in edit_patch:
        changes.append("narration updated")

    if not changes:
        changes.append("details updated")

    return f"Schedule updated: {'; '.join(changes)}. Next run: {format_lagos_schedule_datetime(next_run_at)}."


def apply_schedule_edit(schedule: Any, edit_patch: dict[str, Any], next_run_at: datetime) -> None:
    merged = _merged_schedule_state(schedule, edit_patch)
    schedule.payload_snapshot = merged["payload"]
    schedule.recurrence_type = merged["recurrence_type"]
    schedule.start_date = merged["start_date"]
    schedule.local_time = merged["local_time"]
    schedule.timezone = merged["timezone"]
    schedule.day_of_week = merged["day_of_week"]
    schedule.day_of_month = merged["day_of_month"]
    schedule.end_date = merged["end_date"]
    schedule.next_run_at_utc = next_run_at
    schedule.cancelled_at = None
    schedule.status = ScheduledInstructionStatusEnum.ACTIVE.value


def disambiguation_result(
    schedules: list[Any],
    *,
    action_label: str,
    required_field: str = "schedule_selector",
) -> TransactionResult:
    if not schedules:
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=f"I couldn't find an active scheduled transaction to {action_label}.",
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )
    lines = [f"Reply with the schedule number to {action_label}:"]
    lines.extend(format_schedule_row(index, schedule) for index, schedule in enumerate(schedules, start=1))
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=[required_field],
        prompt="\n".join(lines),
        patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
    )


def cancelled_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
