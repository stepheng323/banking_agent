import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.interrupt_shortcuts import (
    is_explicit_confirmation_approval,
    resolve_shortcut_locale,
)
from shared.types.planner import (
    InterruptRouteDecision,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)
TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320
_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)
_SCOPED_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)


def _resolve_deterministic_confirmation_repeat_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "confirmation":
        return None

    normalized_text = _normalize_recipient_match_text(text)
    if not normalized_text:
        return None

    matched_task_ids: list[str] = []
    for task_id in getattr(interrupt, "task_ids", []) or []:
        task = state.tasks.get(str(task_id))
        if task is None:
            continue
        if normalized_text in _task_request_variants(task):
            matched_task_ids.append(str(task_id))

    if not matched_task_ids:
        return None

    logger.info(
        "interrupt_repeat_in_flow_detected",
        kind=interrupt.kind,
        matched_task_ids=matched_task_ids,
        match_kind="exact_request_repeat",
    )
    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=0.99,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason="shortcut_confirmation_exact_repeat",
    )


def _message_targets_transfer_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    message_digits = _digits_only(message_text)
    recipient_account = _digits_only(str(payload.get("recipient_account") or ""))
    if len(recipient_account) >= 10 and recipient_account in message_digits:
        return True

    recipient_names = [
        str(payload.get("recipient_name") or "").strip(),
        str(payload.get("recipient_resolved_name") or "").strip(),
    ]
    for candidate in recipient_names:
        normalized_candidate = _normalize_recipient_match_text(candidate)
        if not normalized_candidate:
            continue
        if re.search(rf"\b{re.escape(normalized_candidate)}\b", normalized_message):
            return True

        tokens = [token for token in normalized_candidate.split() if len(token) >= 3]
        if len(tokens) < 2:
            continue
        token_hits = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_message))
        if token_hits >= 2:
            return True

    return False


def _message_targets_airtime_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    if re.search(r"\b(airtime|recharge|top up|topup)\b", normalized_message):
        return True

    message_digits = _digits_only(message_text)
    recipient_phone = _digits_only(str(payload.get("recipient_phone") or ""))
    if len(recipient_phone) >= 10 and recipient_phone in message_digits:
        return True

    network = _normalize_recipient_match_text(str(payload.get("network") or ""))
    if network and re.search(rf"\b{re.escape(network)}\b", normalized_message):
        return True

    recipient_name = _normalize_recipient_match_text(str(payload.get("recipient_name") or ""))
    if recipient_name and re.search(rf"\b{re.escape(recipient_name)}\b", normalized_message):
        return True

    return False


def _message_targets_data_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    if re.search(r"\b(data|bundle|plan|mb|gb)\b", normalized_message):
        return True

    message_digits = _digits_only(message_text)
    target_phone = _digits_only(str(payload.get("target_phone") or ""))
    if len(target_phone) >= 10 and target_phone in message_digits:
        return True

    network = _normalize_recipient_match_text(str(payload.get("network") or ""))
    if network and re.search(rf"\b{re.escape(network)}\b", normalized_message):
        return True

    plan_name = _normalize_recipient_match_text(str(payload.get("plan_name") or ""))
    if plan_name and re.search(rf"\b{re.escape(plan_name)}\b", normalized_message):
        return True

    return False


def _message_targets_confirmation_task(message_text: str, task: TaskSpec) -> bool:
    if task.type == "transfer":
        return _message_targets_transfer_task(message_text, task)
    if task.type == "airtime":
        return _message_targets_airtime_task(message_text, task)
    if task.type == "data":
        return _message_targets_data_task(message_text, task)
    return False


def _parse_scoped_confirmation_amount(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    match = _SCOPED_CONFIRMATION_AMOUNT_RE.search(text)
    if match is None:
        return None
    token = match.group(0)
    suffix = token[-1].lower() if token and token[-1].lower() in {"k", "h"} else ""
    number_part = token[:-1] if suffix else token
    normalized = re.sub(r"(?i)(?:₦|ngn)", "", number_part).strip()
    normalized = normalized.replace(",", "")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if suffix == "k":
        amount *= 1000.0
    elif suffix == "h":
        amount *= 100.0
    return amount if amount > 0 else None


def _transfer_task_reference_matches(reference: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_reference = _normalize_recipient_match_text(reference)
    if not normalized_reference:
        return False

    for field in ("recipient_name", "recipient_resolved_name"):
        normalized_candidate = _normalize_recipient_match_text(str(payload.get(field) or ""))
        if not normalized_candidate:
            continue
        if re.search(rf"\b{re.escape(normalized_candidate)}\b", normalized_reference):
            return True
        if re.search(rf"\b{re.escape(normalized_reference)}\b", normalized_candidate):
            return True
    return False


def _transfer_task_amount(task: TaskSpec) -> float | None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    amount = payload.get("amount")
    if isinstance(amount, (int, float)) and amount > 0:
        return float(amount)

    confirmation = payload.get("confirmation")
    snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
    snapshot_amount = snapshot.get("amount") if isinstance(snapshot, dict) else None
    if isinstance(snapshot_amount, (int, float)) and snapshot_amount > 0:
        return float(snapshot_amount)
    return None


def _amount_patch(amount: float) -> dict[str, Any]:
    return {
        "confirmation": {"confirmed": False},
        "amount": amount,
        "transfer_percentage": None,
        "transfer_all": False,
        "funding_plan": None,
        "suggested_amount": None,
    }


def _synth_confirmation_followup_message(task: TaskSpec) -> str | None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    if task.type != "transfer":
        return None

    recipient_name = str(payload.get("recipient_name") or "").strip()
    amount = payload.get("amount")
    narration = str(
        payload.get("authored_narration") or payload.get("narration") or payload.get("user_note") or ""
    ).strip()
    if not recipient_name:
        return None

    amount_text = None
    if isinstance(amount, (int, float)) and float(amount) > 0:
        amount_text = str(int(amount)) if float(amount).is_integer() else str(float(amount))

    base = f"Send {amount_text} to {recipient_name}" if amount_text else f"Send money to {recipient_name}"
    if narration:
        return f"{base} for {narration}"
    return base


def _select_confirmation_continue_flow_task_ids(
    state: OrchestratorState,
    interrupt: Any,
) -> tuple[list[str], str, list[str]]:
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(task_ids) < 2:
        return task_ids, "single_or_empty_batch", []

    message_text = (state.last_message_text or "").strip()
    if not message_text:
        return task_ids, "empty_message", []

    if _CONFIRMATION_COLLECTIVE_SCOPE_RE.search(message_text):
        return task_ids, "collective_scope", []

    matched_task_ids = [
        task_id
        for task_id in task_ids
        if _message_targets_confirmation_task(message_text, state.tasks[task_id])
    ]
    if 0 < len(matched_task_ids) < len(task_ids):
        return matched_task_ids, "matched_subset", matched_task_ids
    if not matched_task_ids:
        return task_ids, "no_recipient_match", []
    return task_ids, "matched_all", matched_task_ids

def _stash_previous_confirmation_snapshots(state: OrchestratorState, task_ids: list[str]) -> None:
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if task is None:
            continue
        confirmation = task.payload.get("confirmation")
        snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            task.payload["previous_confirmation_snapshot"] = dict(snapshot)
        else:
            task.payload.pop("previous_confirmation_snapshot", None)

def _is_explicit_confirmation_approval_text(state: OrchestratorState, text: str) -> bool:
    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    return is_explicit_confirmation_approval(text=text, locale=shortcut_locale)

def _approve_confirmation_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    new_tasks = state.tasks.copy()
    logger.info("confirmation_confirmed", tasks=interrupt.task_ids, via_pin=state.pin_verified)
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        task.payload.setdefault("confirmation", {})
        task.payload["confirmation"]["confirmed"] = True
        task.stage = TaskStage.EXECUTING if state.pin_verified else TaskStage.AWAITING_AUTH
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }


def _normalize_recipient_match_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _digits_only(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def _task_request_variants(task: TaskSpec) -> list[str]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    variants: list[str] = []
    for field in ("message", "instruction"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            variants.append(_normalize_recipient_match_text(value))
    return [variant for variant in variants if variant]
