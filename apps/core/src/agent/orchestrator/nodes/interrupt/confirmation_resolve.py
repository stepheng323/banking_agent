import re
from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.services.interrupt_shortcuts import (
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
_CONFIRMATION_UPDATE_VERB_RE = re.compile(
    r"\b(change|update|edit|instead|set|make(?:\s+it)?|replace|correct|meant|add|use)\b",
    re.IGNORECASE,
)
_CONFIRMATION_UPDATE_FIELD_RE = re.compile(
    r"\b(amount|bank|account|recipient|beneficiary|narration|memo|note|description)\b",
    re.IGNORECASE,
)
_CONFIRMATION_NOTE_FIELD_RE = re.compile(r"\b(narration|memo|note|description|reason|purpose)\b", re.IGNORECASE)
_CONFIRMATION_ITS_FOR_RE = re.compile(r"\b(?:it'?s|its|it is|this is)\s+for\b", re.IGNORECASE)
_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:^|\s)(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?(?:\s|$)",
    re.IGNORECASE,
)
_INPUT_SIMPLE_AMOUNT_REPLY_RE = re.compile(r"^(?:₦?\d[\d,]*(?:\.\d+)?k?|all|everything|half|50%)$", re.IGNORECASE)
_CONFIRMATION_ACCOUNT_BANK_REPLY_RE = re.compile(r"[a-zA-Z].*\d[\d\s,.\-]{8,}|\d[\d\s,.\-]{8,}.*[a-zA-Z]")
_NON_TRANSFER_INTENT_HINT_RE = re.compile(
    r"\b(airtime|data|bundle|balance|statement|support|faq|ticket|complaint)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|this is)\s+)?(?:(?:to|for|send(?:\s+it)?\s+to)\s+)?(?P<recipient>.+?)$",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_BLOCK_RE = re.compile(
    r"\b(and|also|plus|then|while|cancel|stop|show|list|check|buy|help|support|faq|balance|statement|spend|spent|transaction|transactions|airtime|data|beneficiar(?:y|ies)|account(?:s)?|week|month|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_QUESTION_RE = re.compile(r"^(what|how|why|when|where|who|which)\b", re.IGNORECASE)
_INPUT_RECIPIENT_REPLY_META_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)$",
    re.IGNORECASE,
)
_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)
_CONFIRMATION_MULTI_CLAUSE_SPLIT_RE = re.compile(r"\s+(?:and|then)\s+|[;\n]+|,\s*", re.IGNORECASE)
_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
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

def _resolve_deterministic_confirmation_scope_update_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "confirmation":
        return None
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(task_ids) < 2:
        return None
    payload_overrides, _message_overrides, ambiguity_reason = _build_confirmation_task_overrides(
        state,
        task_ids,
        message_text=text,
    )
    if ambiguity_reason is not None:
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type=None,
            reason=f"shortcut_confirmation_scope_clarify:{ambiguity_reason}",
        )
    if not payload_overrides:
        return None
    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=0.99,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason="shortcut_confirmation_scoped_update",
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

def _transfer_task_candidate_patterns(task: TaskSpec) -> list[tuple[str, str]]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    candidates = [
        str(payload.get("recipient_name") or "").strip(),
        str(payload.get("recipient_resolved_name") or "").strip(),
    ]
    patterns: list[tuple[str, str]] = []
    for candidate in candidates:
        if not candidate:
            continue
        escaped = re.escape(candidate)
        patterns.extend(
            [
                (
                    "for_target",
                    rf"^(?:the\s+one|the\s+transfer)\s+for\s+{escaped}\s+"
                    rf"(?:should\s+be|is|as)\s+(?P<value>.+)$",
                ),
                ("for_target", rf"^for\s+{escaped}\s+(?:should\s+be|is|as)\s+(?P<value>.+)$"),
                ("named_target", rf"^{escaped}\s+(?:should\s+be|is|as)\s+(?P<value>.+)$"),
                ("named_target", rf"^{escaped}\s*[:=-]\s*(?P<value>.+)$"),
                (
                    "amount_target",
                    rf"^(?:also\s+)?(?:make|change|update|set)\s+"
                    rf"(?:the\s+one\s+for\s+)?{escaped}\s+"
                    rf"(?:amount\s+)?(?:to\s+)?(?P<value>.+)$",
                ),
            ]
        )
    return patterns

def _normalize_scoped_note_value(value: str) -> str | None:
    note = value.strip(" \t\r\n.,;:!?")
    if not note:
        return None
    if note.isdigit():
        return None
    return note

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

def _extract_scoped_transfer_task_updates(clause: str, task: TaskSpec) -> dict[str, Any] | None:
    cleaned_clause = clause.strip(" \t\r\n.,;:!?")
    if not cleaned_clause:
        return None

    note_value: str | None = None
    amount_value: float | None = None
    for pattern_kind, pattern in _transfer_task_candidate_patterns(task):
        match = re.match(pattern, cleaned_clause, re.IGNORECASE)
        if match is None:
            continue
        raw_value = str(match.group("value") or "").strip()
        if not raw_value:
            continue
        if pattern_kind == "amount_target":
            amount_value = _parse_scoped_confirmation_amount(raw_value)
            continue

        split_parts = re.split(
            r"\s+also\s+(?=(?:make|change|update|set)\b)",
            raw_value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        note_candidate = _normalize_scoped_note_value(split_parts[0] if split_parts else raw_value)
        if note_candidate and _parse_scoped_confirmation_amount(note_candidate) is None:
            note_value = note_candidate
        if len(split_parts) == 2:
            amount_value = _parse_scoped_confirmation_amount(split_parts[1])

    if note_value is None and amount_value is None:
        return None

    patch: dict[str, Any] = {"confirmation": {"confirmed": False}}
    if note_value is not None:
        patch.update(
            {
                "authored_narration": note_value,
                "narration": note_value,
                "user_note": note_value,
            }
        )
    if amount_value is not None:
        patch.update(
            {
                "amount": amount_value,
                "transfer_percentage": None,
                "transfer_all": False,
                "funding_plan": None,
                "suggested_amount": None,
            }
        )
    return patch

def _build_confirmation_task_overrides(
    state: OrchestratorState,
    task_ids: list[str],
    *,
    message_text: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], str | None]:
    if not task_ids:
        return {}, {}, None

    clauses = [part.strip() for part in _CONFIRMATION_MULTI_CLAUSE_SPLIT_RE.split(message_text) if part.strip()]
    if not clauses:
        return {}, {}, None

    payload_overrides: dict[str, dict[str, Any]] = {}
    message_overrides: dict[str, str] = {}

    for clause in clauses:
        matched_task_ids = [
            task_id
            for task_id in task_ids
            if task_id in state.tasks and _message_targets_confirmation_task(clause, state.tasks[task_id])
        ]
        if len(matched_task_ids) != 1:
            if _looks_like_ambiguous_scoped_confirmation_clause(clause):
                return {}, {}, "ambiguous_clause_scope"
            continue

        task_id = matched_task_ids[0]
        task = state.tasks[task_id]
        if task.type == "transfer":
            scoped_patch = _extract_scoped_transfer_task_updates(clause, task)
            if scoped_patch:
                if task_id in payload_overrides:
                    payload_overrides[task_id].update(scoped_patch)
                else:
                    payload_overrides[task_id] = scoped_patch
                continue

    if not payload_overrides and not message_overrides:
        return {}, {}, None
    return payload_overrides, message_overrides, None

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

def _looks_like_ambiguous_scoped_confirmation_clause(clause: str) -> bool:
    if _CONFIRMATION_COLLECTIVE_SCOPE_RE.search(clause):
        return False
    has_scope_reference = bool(re.search(r"\b(?:the\s+one|this\s+one|that\s+one|it)\b", clause, re.IGNORECASE))
    if not has_scope_reference:
        return False
    return bool(
        _CONFIRMATION_UPDATE_VERB_RE.search(clause)
        or _CONFIRMATION_NOTE_FIELD_RE.search(clause)
        or _CONFIRMATION_ITS_FOR_RE.search(clause)
        or re.search(r"\b(?:is|as)\b", clause, re.IGNORECASE)
    )

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
