import re
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.utils.waves import build_dependency_waves
from shared.services.scheduling.recurrence import (
    DEFAULT_SCHEDULE_TIME_TEXT,
    SCHEDULE_TIMEZONE,
    normalize_time_local,
)
from shared.utils.bank_aliases import get_bank_search_terms
from shared.utils.network_utils import normalize_nigerian_phone
from shared.utils.sanitize import normalize_bank_account_number

_TRANSFER_VERB_TOKENS = {"send", "transfer", "pay", "remit"}
_RECIPIENT_NOISE_TOKENS = _TRANSFER_VERB_TOKENS | {"to", "for", "money", "cash", "funds", "s"}
_RECIPIENT_SEGMENT_BOUNDARY = re.compile(r"\b(?:then|from|using|with|via|through|while)\b")
_WEEKDAY_NAME_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_AMOUNT_VALUE_PATTERN = re.compile(r"^\s*(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMhH]?)\s*$")


def apply_source_account_fields(payload: dict[str, Any], plan_item: Any) -> None:
    if plan_item.executor not in ("transfer", "airtime", "data") or not plan_item.parameters:
        return

    if plan_item.parameters.source_bank_name:
        payload["source_bank_name"] = plan_item.parameters.source_bank_name
    if plan_item.parameters.source_account_index is not None:
        payload["source_account_index"] = plan_item.parameters.source_account_index


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _digits_only(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def _recipient_account_grounded_in_user_text(recipient_account: str | None, user_text: str) -> bool:
    account_digits = _digits_only(recipient_account)
    if len(account_digits) < 10:
        return False
    text_digits = _digits_only(user_text)
    if not text_digits:
        return False
    if account_digits in text_digits:
        return True
    normalized_account = normalize_bank_account_number(account_digits) or ""
    return bool(normalized_account and normalized_account in text_digits)


def _recipient_bank_grounded_in_user_text(recipient_bank_name: str | None, user_text: str) -> bool:
    norm_bank = _normalize_text(recipient_bank_name)
    norm_text = _normalize_text(user_text)
    if not norm_bank or not norm_text:
        return False
    if re.search(rf"\b{re.escape(norm_bank)}\b", norm_text):
        return True

    base_bank = re.sub(r"\bbank\b", "", norm_bank).strip()
    if base_bank and re.search(rf"\b{re.escape(base_bank)}\b", norm_text):
        return True

    for term in get_bank_search_terms(recipient_bank_name or ""):
        norm_term = _normalize_text(term)
        if not norm_term:
            continue
        if re.search(rf"\b{re.escape(norm_term)}\b", norm_text):
            return True
    return False


def _is_plausible_recipient_candidate(candidate: str | None) -> bool:
    norm_candidate = _normalize_text(candidate)
    if not norm_candidate:
        return False
    if norm_candidate.isdigit():
        return False
    tokens = [token for token in norm_candidate.split() if token]
    if any(re.fullmatch(r"\d+(?:k|m)?", token) for token in tokens):
        return False
    return not all(token in _RECIPIENT_NOISE_TOKENS for token in tokens)


def _recipient_grounded_in_user_text(recipient: str | None, user_text: str) -> bool:
    """Return True if planner recipient is clearly present in user's original message."""
    norm_recipient = _normalize_text(recipient)
    norm_text = _normalize_text(user_text)
    if not norm_recipient or not norm_text:
        return True
    if not _is_plausible_recipient_candidate(norm_recipient):
        return False
    return norm_recipient in norm_text


def _derive_recipients_from_user_text(user_text: str) -> list[str]:
    """Derive ordered recipient candidates from a transfer utterance."""
    norm_text = _normalize_text(user_text)
    if not norm_text:
        return []

    match = re.search(r"\b(?:to|for|si|ga|zuwa)\b\s+(.+)", norm_text)
    if not match:
        match = re.search(r"\b(?:between|btw)\b\s+(.+)", norm_text)
    if not match:
        return []

    segment = match.group(1).strip()
    if not segment:
        return []

    segment = _RECIPIENT_SEGMENT_BOUNDARY.split(segment, maxsplit=1)[0].strip()
    segment = re.sub(r"\band\s+to\b", " and ", segment)
    if not segment:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for raw_part in re.split(r"\s*(?:,|\band\b)\s*", segment):
        part = re.sub(r"^(?:to|for|si|ga|zuwa)\s+", "", raw_part).strip()
        if not _is_plausible_recipient_candidate(part):
            continue
        key = _normalize_text(part)
        if key and key not in seen:
            seen.add(key)
            candidates.append(part)
    return candidates


def _derive_recipient_from_user_text(planned_recipient: str | None, user_text: str) -> str | None:
    """Derive a safe recipient token from user text when planner over-expands names."""
    norm_planned = _normalize_text(planned_recipient)
    norm_text = _normalize_text(user_text)
    if not norm_text:
        return None

    recipient_candidates = _derive_recipients_from_user_text(user_text)
    if recipient_candidates:
        return recipient_candidates[0]

    planned_tokens = [token for token in norm_planned.split() if token]
    text_tokens = {token for token in norm_text.split() if token}
    for token in planned_tokens:
        if token in text_tokens and _is_plausible_recipient_candidate(token):
            return token

    return None


def _apply_transfer_payload_fields(
    payload: dict[str, Any],
    plan_item: Any,
    fallback_message: str,
    *,
    strip_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
) -> None:
    if plan_item.executor != "transfer":
        return

    action_name = str(payload.get("action") or "")

    if plan_item.parameters and plan_item.parameters.reference:
        payload["recipient_reference"] = plan_item.parameters.reference.model_dump(exclude_none=True)

    # Planner schema uses `bank_name`; transfer runtime expects `recipient_bank_name`.
    bank_name = payload.pop("bank_name", None)
    if bank_name and not payload.get("recipient_bank_name"):
        payload["recipient_bank_name"] = bank_name
    payload.pop("recipient_allocations", None)

    if "recipient_account" in payload:
        normalized_account = normalize_bank_account_number(payload.get("recipient_account"))
        if normalized_account:
            payload["recipient_account"] = normalized_account

    has_recipient_field = "recipient" in payload
    if has_recipient_field:
        recipient_val = payload.pop("recipient")
        if strip_recipient_suffix and isinstance(recipient_val, str) and recipient_val:
            recipient_val = recipient_val.rstrip("},. ")
        recipient_val_str = str(recipient_val) if recipient_val is not None else None
        if not payload.get("recipient_name"):
            if _recipient_grounded_in_user_text(recipient_val_str, fallback_message):
                payload["recipient_name"] = recipient_val
            else:
                derived = _derive_recipient_from_user_text(recipient_val_str, fallback_message)
                if derived:
                    payload["recipient_name"] = derived

    # Guard against planner hallucinating a fully-resolved name not present in user text.
    recipient_name = payload.get("recipient_name")
    if (
        isinstance(recipient_name, str)
        and recipient_name
        and not _recipient_grounded_in_user_text(
            recipient_name,
            fallback_message,
        )
    ):
        derived = _derive_recipient_from_user_text(recipient_name, fallback_message)
        if derived:
            payload["recipient_name"] = derived
        else:
            payload.pop("recipient_name", None)

    # Guard destination fields against stale planner context leakage.
    recipient_account = payload.get("recipient_account")
    if isinstance(recipient_account, str) and recipient_account:
        if not _recipient_account_grounded_in_user_text(recipient_account, fallback_message):
            payload.pop("recipient_account", None)
            payload.pop("recipient_resolved_name", None)
            payload.pop("recipient_bank_code", None)

    recipient_bank_name = payload.get("recipient_bank_name")
    if isinstance(recipient_bank_name, str) and recipient_bank_name:
        if not _recipient_bank_grounded_in_user_text(recipient_bank_name, fallback_message):
            payload.pop("recipient_bank_name", None)
            payload.pop("recipient_bank_code", None)
            payload.pop("recipient_resolved_name", None)

    if format_narration_requires_recipient_field and not has_recipient_field:
        return

    from shared.utils.narration import format_narration

    payload["narration"] = format_narration(
        payload.get("narration"),
        payload.get("recipient_resolved_name") or payload.get("recipient_name"),
    )

    inferred_schedule_action = _infer_schedule_action_from_text(fallback_message)
    if action_name == "send_money" and inferred_schedule_action:
        payload["action"] = inferred_schedule_action
        action_name = inferred_schedule_action

    if action_name in {"schedule_transfer", "recurring_transfer"}:
        schedule_patch = _derive_transfer_schedule_fields(
            fallback_message,
            schedule_text=(plan_item.parameters.schedule if plan_item.parameters else None),
            scheduled_text=(plan_item.parameters.scheduled if plan_item.parameters else None),
            recurring_flag=(plan_item.parameters.recurring if plan_item.parameters else None),
        )
        payload.update(schedule_patch)
    elif action_name == "cancel_scheduled_transfer":
        if payload.get("schedule_id") and not payload.get("schedule_selector"):
            payload["schedule_selector"] = payload.get("schedule_id")
        schedule_selector = _derive_schedule_selector_from_user_text(fallback_message)
        if schedule_selector:
            payload["schedule_selector"] = schedule_selector


def _apply_airtime_payload_fields(payload: dict[str, Any], plan_item: Any) -> None:
    if plan_item.executor != "airtime":
        return
    phone = payload.get("phone")
    if isinstance(phone, str) and phone.strip() and not payload.get("recipient_phone"):
        payload["recipient_phone"] = phone


def _parse_amount_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    match = _AMOUNT_VALUE_PATTERN.match(text)
    if not match:
        return None

    try:
        numeric = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    multiplier = 1.0
    if suffix == "k":
        multiplier = 1000.0
    elif suffix == "h":
        multiplier = 100.0
    elif suffix == "m":
        multiplier = 1_000_000.0
    amount = numeric * multiplier
    if amount <= 0:
        return None
    return amount


def _apply_data_payload_fields(payload: dict[str, Any], plan_item: Any) -> None:
    if plan_item.executor != "data":
        return

    target_phone = payload.get("target_phone")
    recipient_phone = payload.get("recipient_phone")
    phone = payload.get("phone")

    if not target_phone and isinstance(recipient_phone, str) and recipient_phone.strip():
        normalized = normalize_nigerian_phone(recipient_phone) or recipient_phone.strip()
        payload["target_phone"] = normalized
    elif not target_phone and isinstance(phone, str) and phone.strip():
        normalized = normalize_nigerian_phone(phone) or phone.strip()
        payload["target_phone"] = normalized

    if (
        "plan" in payload
        and isinstance(payload.get("plan"), str)
        and payload.get("plan")
        and not payload.get("plan_name")
    ):
        payload["plan_name"] = payload.get("plan")

    if payload.get("amount") is None and payload.get("budget") is not None:
        parsed_budget = _parse_amount_value(payload.get("budget"))
        if parsed_budget is not None:
            payload["amount"] = parsed_budget


def _infer_schedule_action_from_text(user_text: str) -> str | None:
    normalized = user_text.lower()
    if re.search(r"\b(?:every|daily|weekly|monthly)\b", normalized):
        return "recurring_transfer"
    if re.search(r"\b(?:tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b", normalized):
        return "schedule_transfer"
    return None


def _derive_schedule_selector_from_user_text(user_text: str) -> str | None:
    match = re.search(r"\b(?:schedule\s+)?(\d{1,3})\b", user_text.lower())
    if match:
        return match.group(1)
    return None


def _derive_transfer_schedule_fields(
    user_text: str,
    *,
    schedule_text: str | None,
    scheduled_text: str | None,
    recurring_flag: bool | None,
) -> dict[str, Any]:
    raw_text = " ".join(filter(None, [user_text, schedule_text, scheduled_text])).strip().lower()
    now_local = datetime.now(UTC) + timedelta(hours=1)  # Africa/Lagos UTC+1

    time_local = _extract_time_local(raw_text) or DEFAULT_SCHEDULE_TIME_TEXT
    is_recurring = bool(recurring_flag) or "every " in raw_text or "daily" in raw_text or "weekly" in raw_text
    recurrence_type = "one_time"
    schedule_mode = "one_time"
    schedule_start_date: str | None = None
    schedule_day_of_week: int | None = None
    schedule_day_of_month: int | None = None

    if is_recurring:
        schedule_mode = "recurring"
        recurrence_type = "daily"

        weekday_match = re.search(
            r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            raw_text,
        )
        if weekday_match:
            recurrence_type = "weekly"
            schedule_day_of_week = _WEEKDAY_NAME_TO_INDEX.get(weekday_match.group(1))

        monthly_match = re.search(r"\bevery\s+month(?:\s+on)?\s+(\d{1,2})(?:st|nd|rd|th)?\b", raw_text)
        if monthly_match:
            recurrence_type = "monthly"
            schedule_day_of_month = max(1, min(31, int(monthly_match.group(1))))

        if "every day" in raw_text or "daily" in raw_text:
            recurrence_type = "daily"

        schedule_start_date = _extract_date(raw_text, now_local=now_local) or now_local.date().isoformat()
    else:
        recurrence_type = "one_time"
        schedule_mode = "one_time"
        schedule_start_date = _extract_date(raw_text, now_local=now_local)

    patch: dict[str, Any] = {
        "schedule_mode": schedule_mode,
        "recurrence_type": recurrence_type,
        "schedule_timezone": SCHEDULE_TIMEZONE,
        "schedule_time_local": time_local,
    }
    if schedule_start_date:
        patch["schedule_start_date"] = schedule_start_date
    if schedule_day_of_week is not None:
        patch["schedule_day_of_week"] = schedule_day_of_week
    if schedule_day_of_month is not None:
        patch["schedule_day_of_month"] = schedule_day_of_month
    return patch


def _extract_time_local(text: str) -> str | None:
    ampm_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if ampm_match:
        hour = int(ampm_match.group(1)) % 12
        minute = int(ampm_match.group(2) or 0)
        if ampm_match.group(3) == "pm":
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    hour_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if hour_match:
        normalized = normalize_time_local(f"{hour_match.group(1)}:{hour_match.group(2)}")
        return normalized
    return None


def _extract_date(text: str, *, now_local: datetime) -> str | None:
    if "tomorrow" in text:
        return (now_local.date() + timedelta(days=1)).isoformat()
    if "today" in text:
        return now_local.date().isoformat()

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)

    dmy_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if dmy_match:
        day = int(dmy_match.group(1))
        month = int(dmy_match.group(2))
        year = int(dmy_match.group(3))
        try:
            return datetime(year=year, month=month, day=day).date().isoformat()
        except ValueError:
            return None
    return None


def build_task_spec_from_plan_item(
    plan_item: Any,
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
) -> TaskSpec:
    payload = plan_item.parameters.model_dump() if plan_item.parameters else {}

    if plan_item.action:
        if preserve_existing_action_instruction:
            payload.setdefault("action", plan_item.action)
        else:
            payload["action"] = plan_item.action

    if plan_item.instruction:
        if preserve_existing_action_instruction:
            payload.setdefault("instruction", plan_item.instruction)
        else:
            payload["instruction"] = plan_item.instruction

    if plan_item.executor == "query" and not payload.get("message"):
        payload["message"] = fallback_message or plan_item.instruction or ""

    if include_skip_extraction and plan_item.executor in ("transfer", "airtime", "data"):
        payload["skip_extraction"] = True

    _apply_transfer_payload_fields(
        payload,
        plan_item,
        fallback_message,
        strip_recipient_suffix=strip_transfer_recipient_suffix,
        format_narration_requires_recipient_field=format_narration_requires_recipient_field,
    )
    _apply_airtime_payload_fields(payload, plan_item)
    _apply_data_payload_fields(payload, plan_item)
    apply_source_account_fields(payload, plan_item)

    return TaskSpec(
        id=plan_item.task_id,
        type=plan_item.executor,
        depends_on=list(plan_item.depends_on or []),
        stage=TaskStage.DRAFT,
        payload=payload,
    )


def build_task_specs_from_plan_items(
    plan_items: list[Any],
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
    payload_overrides_by_task_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TaskSpec]:
    task_specs: dict[str, TaskSpec] = {}
    for plan_item in plan_items:
        spec = build_task_spec_from_plan_item(
            plan_item,
            fallback_message,
            preserve_existing_action_instruction=preserve_existing_action_instruction,
            include_skip_extraction=include_skip_extraction,
            strip_transfer_recipient_suffix=strip_transfer_recipient_suffix,
            format_narration_requires_recipient_field=format_narration_requires_recipient_field,
        )
        payload_override = (payload_overrides_by_task_id or {}).get(spec.id)
        if payload_override:
            spec.payload.update(payload_override)
        task_specs[spec.id] = spec
    return task_specs


def build_task_specs_and_waves_from_plan_items(
    plan_items: list[Any],
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
    payload_overrides_by_task_id: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, TaskSpec], list[list[str]]]:
    task_specs = build_task_specs_from_plan_items(
        plan_items,
        fallback_message,
        preserve_existing_action_instruction=preserve_existing_action_instruction,
        include_skip_extraction=include_skip_extraction,
        strip_transfer_recipient_suffix=strip_transfer_recipient_suffix,
        format_narration_requires_recipient_field=format_narration_requires_recipient_field,
        payload_overrides_by_task_id=payload_overrides_by_task_id,
    )
    task_ids = list(task_specs.keys())
    depends_on_by_task = {task_id: list(spec.depends_on) for task_id, spec in task_specs.items()}
    waves = build_dependency_waves(task_ids, depends_on_by_task)
    return task_specs, waves
