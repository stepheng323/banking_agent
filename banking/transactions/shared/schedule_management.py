"""Shared scheduled transaction management helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    normalize_time_local,
    today_lagos,
)
from banking.transactions.shared.scheduling import (
    format_schedule_confirmation_line,
    schedule_recurrence_label,
)
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.money import naira_to_json, to_naira

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


def _dynamic_message_key(key: str, locale: str, *, fallback_en: str) -> str:
    return render_message(cast(MessageKey, key), locale, fallback_en=fallback_en)


def _domain_label(domain: str, locale: str = "en") -> str:
    return _dynamic_message_key(f"schedule.domain.{domain}", locale, fallback_en=domain.title())


def _recurrence_value_label(recurrence: Any, locale: str = "en") -> str:
    return schedule_recurrence_label(recurrence, locale)


def _recurrence_label(schedule: Any, locale: str = "en") -> str:
    return _recurrence_value_label(getattr(schedule, "recurrence_type", None), locale)


def _time_label(schedule: Any, locale: str = "en") -> str:
    local_time = str(getattr(schedule, "local_time", "") or _payload(schedule).get("schedule_time_local") or "")
    normalized = normalize_time_local(local_time) or local_time
    try:
        return datetime.strptime(normalized, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return normalized or render_message("schedule.fallback.time_not_set", locale)


def _time_value_label(value: Any, locale: str = "en") -> str:
    normalized = normalize_time_local(str(value or "")) or str(value or "")
    try:
        return datetime.strptime(normalized, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return normalized or render_message("schedule.fallback.not_set", locale)


def _format_schedule_amount(value: Any) -> str | None:
    amount = to_naira(value)
    if amount is None or amount <= 0:
        return None
    return format_naira(amount)


def _target_label(schedule: Any, locale: str = "en") -> str:
    payload = _payload(schedule)
    domain = _domain(schedule)
    if domain == "airtime":
        phone = (
            payload.get("recipient_phone")
            or payload.get("phone")
            or render_message("schedule.fallback.recipient", locale)
        )
        network = payload.get("network")
        if network:
            return render_message(
                "schedule.target.airtime_with_network",
                locale,
                {"network": str(network), "phone": str(phone)},
            )
        return render_message("schedule.target.airtime", locale, {"phone": str(phone)})
    if domain == "data":
        phone = (
            payload.get("target_phone")
            or payload.get("recipient_phone")
            or render_message(
                "schedule.fallback.recipient",
                locale,
            )
        )
        plan = payload.get("plan_name") or payload.get("plan") or render_message("schedule.fallback.data_plan", locale)
        network = payload.get("network")
        if network:
            return render_message(
                "schedule.target.data_with_network",
                locale,
                {"plan": str(plan), "network": str(network), "phone": str(phone)},
            )
        return render_message("schedule.target.data", locale, {"plan": str(plan), "phone": str(phone)})
    return (
        str(payload.get("recipient_resolved_name") or payload.get("recipient_name") or "").strip()
        or str(payload.get("recipient_account") or "").strip()
        or render_message("schedule.fallback.recipient", locale)
    )


def _amount_label(schedule: Any, locale: str = "en") -> str:
    payload = _payload(schedule)
    return _format_schedule_amount(payload.get("amount")) or render_message("schedule.fallback.scheduled", locale)


def format_schedule_row(index: int, schedule: Any, *, include_id: bool = False, locale: str = "en") -> str:
    domain = _domain_label(_domain(schedule), locale)
    line = render_message(
        "schedule.row.basic",
        locale,
        {
            "index": index,
            "domain": domain,
            "amount": _amount_label(schedule, locale),
            "target": _target_label(schedule, locale),
            "recurrence": _recurrence_label(schedule, locale),
            "time": _time_label(schedule, locale),
        },
    )
    if include_id:
        line = render_message("schedule.row.with_id", locale, {"row": line, "schedule_id": str(schedule.id)})
    return line


def build_schedule_context_items(schedules: list[Any], *, locale: str = "en") -> list[dict[str, Any]]:
    """Build serializable context-frame rows for scheduled transaction follow-ups."""
    items: list[dict[str, Any]] = []
    for index, schedule in enumerate(schedules, start=1):
        payload = _payload(schedule)
        amount_raw = payload.get("amount")
        amount_money = to_naira(amount_raw)
        amount = _format_schedule_amount(amount_raw)
        next_run_at_utc = getattr(schedule, "next_run_at_utc", None)
        next_run = format_lagos_schedule_datetime(next_run_at_utc) if isinstance(next_run_at_utc, datetime) else None
        row = format_schedule_row(index, schedule, include_id=False, locale=locale)
        label = row.split(". ", 1)[1] if row.startswith(f"{index}. ") else row
        data: dict[str, Any] = {
            "type": "scheduled_transaction",
            "schedule_id": str(getattr(schedule, "id", "")),
            "domain": _domain_label(_domain(schedule), locale),
            "domain_key": _domain(schedule),
            "amount": amount,
            "amount_value": naira_to_json(amount_money),
            "target": _target_label(schedule, locale),
            "recurrence": _recurrence_label(schedule, locale),
            "schedule_time": render_message(
                "schedule.time.with_timezone", locale, {"time": _time_label(schedule, locale)}
            ),
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
        "recurrence_type": str(
            edit_patch.get("recurrence_type") or getattr(schedule, "recurrence_type", None) or "one_time"
        ),
        "start_date": str(
            edit_patch.get("schedule_start_date") or getattr(schedule, "start_date", None) or today_lagos().isoformat()
        ),
        "local_time": str(edit_patch.get("schedule_time_local") or getattr(schedule, "local_time", None) or ""),
        "timezone": str(
            edit_patch.get("schedule_timezone") or getattr(schedule, "timezone", None) or SCHEDULE_TIMEZONE
        ),
        "day_of_week": edit_patch.get("schedule_day_of_week", getattr(schedule, "day_of_week", None)),
        "day_of_month": edit_patch.get("schedule_day_of_month", getattr(schedule, "day_of_month", None)),
        "end_date": edit_patch.get("schedule_end_date", getattr(schedule, "end_date", None)),
        "payload": merged,
    }


def build_schedule_update_summary(
    schedule: Any,
    edit_patch: dict[str, Any],
    *,
    locale: str = "en",
) -> tuple[str, dict[str, Any], datetime | None]:
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
    schedule_line = format_schedule_confirmation_line(confirmation_data, locale) or render_message(
        "schedule.update.time_pending",
        locale,
    )
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
            format_schedule_row(1, preview, include_id=False, locale=locale).removeprefix("1. "),
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


def build_schedule_edit_success_message(
    schedule: Any,
    edit_patch: dict[str, Any],
    next_run_at: datetime,
    *,
    locale: str = "en",
) -> str:
    """Render a concise success message that states what changed."""
    payload = _payload(schedule)
    changes: list[str] = []

    if "amount" in edit_patch:
        old_amount = payload.get("amount")
        new_amount = edit_patch.get("amount")
        old_amount_text = _format_schedule_amount(old_amount)
        new_amount_text = _format_schedule_amount(new_amount)
        if old_amount_text and new_amount_text:
            changes.append(
                render_message(
                    "schedule.edit.change.amount_from_to",
                    locale,
                    {"old": old_amount_text, "new": new_amount_text},
                )
            )
        elif new_amount is not None:
            changes.append(
                render_message(
                    "schedule.edit.change.amount_to",
                    locale,
                    {"new": new_amount_text or str(new_amount)},
                )
            )

    if "schedule_time_local" in edit_patch:
        old_time = getattr(schedule, "local_time", None)
        new_time = edit_patch.get("schedule_time_local")
        if str(old_time or "") != str(new_time or ""):
            changes.append(
                render_message(
                    "schedule.edit.change.time_from_to",
                    locale,
                    {"old": _time_value_label(old_time, locale), "new": _time_value_label(new_time, locale)},
                )
            )

    if "schedule_start_date" in edit_patch:
        old_date = getattr(schedule, "start_date", None)
        new_date = edit_patch.get("schedule_start_date")
        if str(old_date or "") != str(new_date or ""):
            changes.append(
                render_message(
                    "schedule.edit.change.date_from_to",
                    locale,
                    {"old": str(old_date or render_message("schedule.fallback.not_set", locale)), "new": str(new_date)},
                )
            )

    if "recurrence_type" in edit_patch:
        old_recurrence = _recurrence_value_label(getattr(schedule, "recurrence_type", None), locale)
        new_recurrence = _recurrence_value_label(edit_patch.get("recurrence_type"), locale)
        if old_recurrence != new_recurrence:
            changes.append(
                render_message(
                    "schedule.edit.change.recurrence_from_to",
                    locale,
                    {"old": old_recurrence, "new": new_recurrence},
                )
            )

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
        changes.append(render_message("schedule.edit.change.target_updated", locale))

    source_fields = (
        "source_account_id",
        "source_bank_name",
        "source_account_name",
        "source_account_number",
        "source_account_index",
    )
    if any(field in edit_patch for field in source_fields):
        changes.append(render_message("schedule.edit.change.source_updated", locale))

    if "narration" in edit_patch:
        changes.append(render_message("schedule.edit.change.narration_updated", locale))

    if not changes:
        changes.append(render_message("schedule.edit.change.details_updated", locale))

    return render_message(
        "schedule.edit.success",
        locale,
        {
            "changes": render_message("schedule.edit.change.separator", locale).join(changes),
            "next_run": format_lagos_schedule_datetime(next_run_at),
        },
    )


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
    locale: str = "en",
) -> TransactionResult:
    if not schedules:
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("schedule.disambiguation.not_found_for_action", locale, {"action": action_label}),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )
    lines = [render_message("schedule.disambiguation.prompt", locale, {"action": action_label})]
    lines.extend(
        format_schedule_row(index, schedule, locale=locale) for index, schedule in enumerate(schedules, start=1)
    )
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=[required_field],
        prompt="\n".join(lines),
        patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
    )


def cancelled_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
