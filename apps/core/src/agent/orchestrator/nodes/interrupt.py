import json
import re
import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
)
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    INTERRUPT_CONTEXT_MAX_CHARS,
    build_interrupt_context_from_summary,
    build_router_context_from_summary,
    get_or_build_turn_context_summary,
)
from apps.core.src.agent.orchestrator.nodes.planner_postprocess import _expand_underproduced_transfer_tasks
from apps.core.src.agent.orchestrator.services.interrupt_shortcuts import (
    is_explicit_confirmation_approval,
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from apps.core.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload
from apps.core.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from apps.core.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from shared.formatters.confirmation import build_confirmation_summary
from shared.formatters.prompts import format_auth_reason, sanitize_recipient_display_name
from shared.formatters.recipient_display import format_recipient_display_label
from shared.i18n import LocaleManager, render_message
from shared.types.planner import (
    InterruptRouteDecision,
    PlannedTask,
    PlannerOutput,
    RecipientAllocation,
    SemanticRouteDecision,
    TaskParameters,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700

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
_CONFIRMATION_ACCOUNT_BANK_REPLY_RE = re.compile(r"[a-zA-Z].*\d[\d\s,.\-]{8,}|\d[\d\s,.\-]{8,}.*[a-zA-Z]")
_NON_TRANSFER_INTENT_HINT_RE = re.compile(
    r"\b(airtime|data|bundle|balance|statement|support|faq|ticket|complaint)\b",
    re.IGNORECASE,
)
_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)
_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 16:
        return value[:max_chars]
    return value[: max_chars - 15].rstrip() + " ...[truncated]"


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize((state.loaded_context or {}).get("language")).value)


def _current_task_types(state: OrchestratorState, task_ids: list[str]) -> set[str]:
    return {state.tasks[tid].type for tid in task_ids if tid in state.tasks}


def _active_intent(current_task_types: set[str]) -> str:
    return next(iter(current_task_types)) if current_task_types else "unknown"


def _callback_flow_type(state: OrchestratorState) -> str | None:
    callback = state.last_callback or {}
    raw_flow_type = callback.get("flow_type")
    if not isinstance(raw_flow_type, str):
        return None
    flow_type = raw_flow_type.strip().lower()
    return flow_type or None


def _is_verified_pin_callback(state: OrchestratorState) -> bool:
    callback = state.last_callback
    if not isinstance(callback, dict):
        return False
    return bool(callback.get("pin_verified")) and state.pin_verified


def _callback_flow_matches_interrupt(
    state: OrchestratorState,
    current_task_types: set[str],
) -> tuple[bool, str | None]:
    callback_flow_type = _callback_flow_type(state)
    if not callback_flow_type:
        return True, None
    if not current_task_types:
        return False, callback_flow_type
    return callback_flow_type in current_task_types, callback_flow_type


def _is_transaction_intent(intent: str | None) -> bool:
    return bool(intent and intent in TRANSACTION_INTENTS)


def _should_stash_switch(current_task_types: set[str], new_task_types: set[str]) -> bool:
    return (
        bool(current_task_types)
        and current_task_types.issubset(TRANSACTION_INTENTS)
        and not new_task_types.issubset(TRANSACTION_INTENTS)
    )


def _is_transaction_replacement(
    *,
    current_task_types: set[str],
    new_task_types: set[str],
    primary_intent: str | None,
) -> bool:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        return False
    if _is_transaction_intent(primary_intent):
        return True
    return bool(new_task_types) and new_task_types.issubset(TRANSACTION_INTENTS)


def _is_resumable_interrupt(interrupt: Any) -> bool:
    kind = getattr(interrupt, "kind", None)
    task_ids = getattr(interrupt, "task_ids", None)
    return kind in {"input", "confirmation", "auth"} and isinstance(task_ids, list) and bool(task_ids)


def _build_interrupt_context(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> str:
    summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=state.stashed_query_session if isinstance(state.stashed_query_session, dict) else None,
        query_session_source="stashed" if isinstance(state.stashed_query_session, dict) else None,
        path_label="interrupt_path",
    )
    active_task_state = _build_active_task_router_state(state=state, task_ids=task_ids)
    active_task_state_text = _clip_text(
        json.dumps(active_task_state, ensure_ascii=True),
        INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS,
    )
    required_fields_text = _clip_text(
        json.dumps(fields_by_task, ensure_ascii=True),
        INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
    )
    prompt_text = _clip_text(prompt or "", INTERRUPT_PROMPT_MAX_CHARS)
    context = build_interrupt_context_from_summary(
        summary,
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
        active_task_state_json=active_task_state_text,
        required_fields_json=required_fields_text,
        prompt_text=prompt_text,
    )
    logger.info("interrupt_context_size", chars=len(context), truncated=len(context) >= INTERRUPT_CONTEXT_MAX_CHARS)
    return context


def _route_fallback(reason: str) -> InterruptRouteDecision:
    return InterruptRouteDecision(
        decision="unclear",
        confidence=0.0,
        detected_language=None,
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason=reason,
    )


def _resolve_deterministic_status_query_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language")) or resolve_shortcut_locale(
        _state_locale(state)
    )
    shortcut_route, _miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )
    if shortcut_route is None or shortcut_route.decision != "status_query":
        return None
    logger.info(
        "interrupt_status_query_shortcut_hit",
        kind=interrupt.kind,
        status_query_type=shortcut_route.status_query_type,
        locale=shortcut_locale.value if shortcut_locale else None,
    )
    return shortcut_route


def _compact_task_payload_for_interrupt_router(payload: dict[str, Any]) -> dict[str, Any]:
    # Keep only stable routing signals to avoid noisy or sensitive prompt context.
    compact: dict[str, Any] = {}
    scalar_fields = (
        "action",
        "amount",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_phone",
        "network",
        "beneficiary_id",
        "source_account_id",
        "source_bank_name",
        "source_account_number",
    )
    for field in scalar_fields:
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            compact[field] = value

    # Expose destination presence semantically without copying raw account digits into prompt context.
    compact["has_recipient_account"] = bool(payload.get("recipient_account"))
    compact["has_recipient_bank_name"] = bool(payload.get("recipient_bank_name"))

    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict):
        summary = confirmation.get("summary")
        snapshot = confirmation.get("snapshot")
        confirmation_view: dict[str, Any] = {}
        if isinstance(summary, str) and summary:
            confirmation_view["summary"] = summary
        if isinstance(snapshot, dict):
            confirmation_view["snapshot"] = {
                key: snapshot.get(key)
                for key in (
                    "amount",
                    "recipient_name",
                    "recipient_phone",
                    "recipient_account",
                    "recipient_bank_name",
                    "sourceBank",
                    "sourceAccount",
                )
                if key in snapshot and isinstance(snapshot.get(key), (str, int, float, bool))
            }
        if confirmation_view:
            compact["confirmation"] = confirmation_view

    return compact


def _build_active_task_router_state(*, state: OrchestratorState, task_ids: list[str]) -> dict[str, Any]:
    task_state: dict[str, Any] = {}
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if not task:
            continue
        task_state[task_id] = {
            "type": str(task.type),
            "stage": str(task.stage),
            "payload": _compact_task_payload_for_interrupt_router(cast(dict[str, Any], task.payload)),
        }
    return task_state


def _is_beneficiary_clarification_interrupt(interrupt: Any) -> bool:
    if not interrupt or getattr(interrupt, "kind", None) != "input":
        return False
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return False
    return any(isinstance(fields, list) and "beneficiary_id" in fields for fields in fields_by_task.values())


async def _route_interrupt(
    *,
    task_planner: Any,
    state: OrchestratorState,
    text: str,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> InterruptRouteDecision:
    if not text:
        return _route_fallback("empty_text")
    if not task_planner or not hasattr(task_planner, "route_pending_input"):
        return _route_fallback("router_unavailable")

    try:
        route_context = _build_interrupt_context(
            state=state,
            kind=kind,
            task_ids=task_ids,
            current_task_types=current_task_types,
            fields_by_task=fields_by_task,
            prompt=prompt,
        )
        try:
            route = await task_planner.route_pending_input(
                state.phone_number,
                text,
                context=route_context,
                path_label="interrupt_path",
            )
        except TypeError:
            route = await task_planner.route_pending_input(
                state.phone_number,
                text,
                context=route_context,
            )
        route = cast(InterruptRouteDecision, route)
        logger.info(
            "interrupt_router_decision",
            kind=kind,
            decision=route.decision,
            confidence=route.confidence,
            detected_language=route.detected_language,
            target_intent=route.target_intent,
            target_mode=route.target_mode,
            status_query_type=route.status_query_type,
        )
        return route
    except Exception as exc:
        logger.warning("interrupt_router_failed", kind=kind, error=str(exc))
        return _route_fallback("router_failed")


async def _route_interrupt_semantic_turn(
    *,
    task_planner: Any,
    state: OrchestratorState,
    text: str,
) -> SemanticRouteDecision | None:
    if not task_planner or not hasattr(task_planner, "route_semantic_turn"):
        return None

    stashed_query_session = state.stashed_query_session if isinstance(state.stashed_query_session, dict) else None
    summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=stashed_query_session,
        query_session_source="stashed" if stashed_query_session is not None else None,
        path_label="interrupt_path",
    )
    semantic_context = build_router_context_from_summary(
        summary,
        expected_executors=state.preplanner_expected_transaction_executors,
    )
    try:
        return cast(
            SemanticRouteDecision,
            await task_planner.route_semantic_turn(
                state.phone_number,
                text,
                context=semantic_context,
                path_label="interrupt_path",
            ),
        )
    except TypeError:
        return cast(
            SemanticRouteDecision,
            await task_planner.route_semantic_turn(
                state.phone_number,
                text,
                context=semantic_context,
            ),
        )
    except Exception as exc:
        logger.warning("interrupt_semantic_router_failed", error=str(exc))
        return None


def _clear_current_domain_sessions(state: OrchestratorState, domains: set[str]) -> tuple[list[Any], str | None]:
    if not domains:
        stack = list(state.session_stack)
        return stack, (stack[-1].domain if stack else None)

    stack = [session for session in state.session_stack if session.domain not in domains]
    return stack, (stack[-1].domain if stack else None)


def _next_interrupt_task_id(
    *,
    state: OrchestratorState,
    target_intent: str,
    start_index: int = 1,
) -> str:
    index = max(start_index, 1)
    while True:
        candidate = f"interrupt_{target_intent}_{index}"
        if candidate not in state.tasks:
            return candidate
        index += 1


def _feature_tokens(requested_features: list[Any] | None) -> set[str]:
    tokens: set[str] = set()
    for feature in requested_features or []:
        if hasattr(feature, "value"):
            tokens.add(str(feature.value).strip().upper())
        else:
            tokens.add(str(feature).strip().upper())
    return {token for token in tokens if token}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _interrupt_required_fields(interrupt: Any) -> list[str]:
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_fields in fields_by_task.values():
        if not isinstance(raw_fields, list):
            continue
        for field in raw_fields:
            if not isinstance(field, str) or field in seen:
                continue
            ordered.append(field)
            seen.add(field)
    return ordered


def _build_transaction_extractor_context(
    *,
    state: OrchestratorState,
    interrupt: Any,
    target_intent: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "phone_number": state.phone_number,
        "language": _state_locale(state),
        "accounts": (state.loaded_context or {}).get("accounts", []),
        "beneficiaries": (state.loaded_context or {}).get("beneficiaries", []),
        "required_fields": _interrupt_required_fields(interrupt),
        "previousResponse": getattr(interrupt, "prompt", None),
        "previous_response": getattr(interrupt, "prompt", None),
    }

    if target_intent != "transfer":
        return context

    first_task_id = next(iter(getattr(interrupt, "task_ids", []) or []), None)
    task = state.tasks.get(str(first_task_id)) if first_task_id else None
    payload = task.payload if task and isinstance(task.payload, dict) else {}
    context["known_recipient"] = {
        "recipient_name": payload.get("recipient_name"),
        "recipient_resolved_name": payload.get("recipient_resolved_name"),
        "recipient_account": payload.get("recipient_account"),
        "recipient_bank_name": payload.get("recipient_bank_name"),
    }
    return context


def _infer_transfer_switch_action(text: str, feature_tokens: set[str]) -> str:
    if _TRANSFER_CANCEL_SCHEDULE_RE.search(text):
        return "cancel_scheduled_transfer"
    if "RECURRING" in feature_tokens or _TRANSFER_RECURRING_RE.search(text):
        return "recurring_transfer"
    if "SCHEDULED" in feature_tokens or _TRANSFER_SCHEDULE_RE.search(text):
        return "schedule_transfer"
    return "send_money"


async def _extract_interrupt_switch_entities(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
    target_intent: str,
) -> tuple[dict[str, Any], set[str], str | None]:
    worker = services.get(target_intent)
    extractor = getattr(worker, "extractor", None) if worker else None
    if extractor is None or not hasattr(extractor, "extract"):
        return {}, set(), None

    context = _build_transaction_extractor_context(
        state=state,
        interrupt=interrupt,
        target_intent=target_intent,
    )
    try:
        extraction = await extractor.extract(text, smart_context=context)
    except Exception as exc:
        logger.warning(f"interrupt_switch_{target_intent}_extract_failed", error=str(exc))
        return {}, set(), None

    entities = extraction.entities.model_dump(exclude_none=True) if getattr(extraction, "entities", None) else {}
    correction = getattr(extraction, "correction", None)
    if correction and getattr(correction, "field", None) and getattr(correction, "new_value", None) is not None:
        field = correction.field.value if hasattr(correction.field, "value") else str(correction.field)
        entities[field] = correction.new_value

    features = _feature_tokens(getattr(extraction, "requested_features", None))
    acknowledgment = str(getattr(extraction, "acknowledgment", "") or "").strip() or None
    return entities, features, acknowledgment


async def _seed_transfer_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
) -> tuple[TaskParameters, dict[str, Any], str, bool]:
    parameters = TaskParameters()
    payload_seed: dict[str, Any] = {}
    action = _infer_transfer_switch_action(text, set())
    entities, features, acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="transfer",
    )
    action = _infer_transfer_switch_action(text, features)

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient = recipient_name
        parameters.recipient_name = recipient_name

    recipient_account = str(entities.get("recipient_account") or "").strip()
    if recipient_account:
        parameters.recipient_account = recipient_account
        payload_seed["recipient_account"] = recipient_account

    bank_name = str(entities.get("bank_name") or "").strip()
    if bank_name:
        parameters.bank_name = bank_name
        payload_seed["recipient_bank_name"] = bank_name

    amount = _coerce_float(entities.get("amount"))
    if amount is not None:
        parameters.amount = amount

    narration = str(entities.get("narration") or "").strip()
    if narration:
        parameters.narration = narration

    source_bank_name = str(entities.get("source_bank_name") or "").strip()
    if source_bank_name:
        parameters.source_bank_name = source_bank_name

    source_account_index = entities.get("source_account_index")
    if isinstance(source_account_index, int):
        parameters.source_account_index = source_account_index

    source_accounts = entities.get("source_accounts")
    if isinstance(source_accounts, list):
        normalized_accounts = [str(item).strip() for item in source_accounts if str(item).strip()]
        if normalized_accounts:
            parameters.source_accounts = normalized_accounts

    if entities.get("use_dual_accounts") is not None:
        parameters.use_dual_accounts = bool(entities.get("use_dual_accounts"))

    explicit_split = entities.get("explicit_split")
    if isinstance(explicit_split, dict):
        normalized_split: dict[str, float] = {}
        for key, value in explicit_split.items():
            amount_value = _coerce_float(value)
            if amount_value is None:
                continue
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            normalized_split[normalized_key] = amount_value
        if normalized_split:
            parameters.explicit_split = normalized_split

    recipient_allocations = entities.get("recipient_allocations")
    if isinstance(recipient_allocations, list):
        normalized_allocations: list[RecipientAllocation] = []
        for item in recipient_allocations:
            if not isinstance(item, dict):
                continue
            recipient_name = str(item.get("recipient_name") or "").strip()
            amount_value = _coerce_float(item.get("amount"))
            if not recipient_name or amount_value is None:
                continue
            normalized_allocations.append(RecipientAllocation(recipient_name=recipient_name, amount=amount_value))
        if normalized_allocations:
            parameters.recipient_allocations = normalized_allocations

    recipient_bank_code = str(entities.get("bank_code") or "").strip()
    if recipient_bank_code:
        payload_seed["recipient_bank_code"] = recipient_bank_code

    source_account_id = str(entities.get("source_account_id") or "").strip()
    if source_account_id:
        payload_seed["source_account_id"] = source_account_id

    if acknowledgment:
        payload_seed["transition_acknowledgment"] = acknowledgment

    preseeded = bool(features or entities or payload_seed)
    return parameters, payload_seed, action, preseeded


async def _seed_airtime_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
) -> tuple[TaskParameters, dict[str, Any], bool]:
    parameters = TaskParameters()
    payload_seed: dict[str, Any] = {}
    entities, _features, _acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="airtime",
    )

    amount = _coerce_float(entities.get("amount"))
    if amount is not None:
        parameters.amount = amount

    recipient_phone = str(entities.get("recipient_phone") or "").strip()
    if recipient_phone:
        parameters.recipient_phone = recipient_phone
        parameters.phone = recipient_phone

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient_name = recipient_name

    if entities.get("is_self") is not None:
        parameters.is_self = bool(entities.get("is_self"))

    source_bank_name = str(entities.get("source_bank_name") or "").strip()
    if source_bank_name:
        parameters.source_bank_name = source_bank_name

    source_account_index = entities.get("source_account_index")
    if isinstance(source_account_index, int):
        parameters.source_account_index = source_account_index

    narration = str(entities.get("narration") or "").strip()
    if narration:
        parameters.narration = narration

    network = str(entities.get("network") or "").strip()
    if network:
        payload_seed["network"] = network

    preseeded = bool(entities or payload_seed)
    return parameters, payload_seed, preseeded


async def _seed_data_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
) -> tuple[TaskParameters, dict[str, Any], bool]:
    parameters = TaskParameters()
    payload_seed: dict[str, Any] = {}
    entities, _features, _acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="data",
    )

    budget = _coerce_float(entities.get("budget"))
    if budget is not None:
        parameters.amount = budget

    recipient_phone = str(entities.get("recipient_phone") or "").strip()
    if recipient_phone:
        parameters.recipient_phone = recipient_phone
        payload_seed["target_phone"] = recipient_phone

    network = str(entities.get("network") or "").strip()
    if network:
        payload_seed["network"] = network

    plan_name = str(entities.get("size_preference") or "").strip()
    if plan_name:
        parameters.plan = plan_name
        payload_seed["plan_name"] = plan_name

    if entities.get("is_self") is not None:
        parameters.is_self = bool(entities.get("is_self"))

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient_name = recipient_name

    preseeded = bool(entities or payload_seed)
    return parameters, payload_seed, preseeded


async def _build_enriched_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    interrupt: Any,
    services: dict[str, Any],
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    if target_intent == "transfer":
        parameters, payload_seed, action, preseeded = await _seed_transfer_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )
    elif target_intent == "airtime":
        parameters, payload_seed, preseeded = await _seed_airtime_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )
        action = "buy_airtime"
    else:
        parameters, payload_seed, preseeded = await _seed_data_switch_payload(
            state=state,
            interrupt=interrupt,
            text=text,
            services=services,
        )
        action = "buy_data"

    base_task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)
    planned_tasks = [
        PlannedTask(
            task_id=base_task_id,
            action=action,
            executor=cast(Any, target_intent),
            instruction=text,
            parameters=parameters,
            risk="MONEY_MOVE",
        )
    ]

    if target_intent == "transfer" and action == "send_money":
        planned_tasks, _ = _expand_underproduced_transfer_tasks(planned_tasks, text)

    payload_overrides_by_task_id: dict[str, dict[str, Any]] = {
        task.task_id: {"message": text} for task in planned_tasks
    }
    if len(planned_tasks) == 1 and payload_seed:
        payload_overrides_by_task_id[planned_tasks[0].task_id].update(payload_seed)
    new_tasks, waves = build_task_specs_and_waves_from_plan_items(
        planned_tasks,
        text,
        preserve_existing_action_instruction=True,
        include_skip_extraction=preseeded,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
        payload_overrides_by_task_id=payload_overrides_by_task_id,
    )
    return new_tasks, waves, {target_intent}


def _build_direct_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)
    task = TaskSpec(
        id=task_id,
        type=cast(Any, target_intent),
        stage=TaskStage.DRAFT,
        payload={
            "instruction": text,
            "message": text,
        },
    )
    return {task_id: task}, [[task_id]], {target_intent}


def _build_direct_non_transaction_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    route: InterruptRouteDecision,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)

    payload: dict[str, Any] = {
        "instruction": text,
        "message": text,
    }
    if target_intent == "query":
        payload["message"] = text
        payload["force_new_query"] = route.target_mode != "continuation"

    task = TaskSpec(
        id=task_id,
        type=cast(Any, target_intent),
        depends_on=[],
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return {task_id: task}, [[task_id]], {target_intent}


def _stash_current_session(
    state: OrchestratorState,
    *,
    interrupt: Any,
    intent: str,
) -> list[dict[str, Any]]:
    current_session = {
        "tasks": state.tasks,
        "waves": state.waves,
        "current_wave_index": state.current_wave_index,
        "pending_interrupt": interrupt,
        "intent": intent,
        "stashed_at_ts": int(time.time()),
    }
    return cast(list[dict[str, Any]], state.stashed_sessions + [current_session])


def _build_stash_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)

    logger.info(
        "interrupt_replan_switched",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        stashed=True,
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "stashed_sessions": stashed,
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


def _build_replace_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    logger.info(
        "interrupt_replan_replaced",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


def _build_transaction_replacement_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack = [session for session in state.session_stack if session.domain not in TRANSACTION_INTENTS]
    logger.info(
        "interrupt_transaction_replaced",
        kind=interrupt.kind,
        cancelled_task_ids=interrupt.task_ids,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


def _build_planner_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    text: str,
    expected_executors: list[str],
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": None,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
    }
    if expected_executors:
        updates["preplanner_expected_transaction_executors"] = expected_executors

    if current_task_types.issubset(TRANSACTION_INTENTS) and _is_resumable_interrupt(interrupt):
        stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
        updates["stashed_sessions"] = stashed
        logger.info(
            "interrupt_switch_to_planner_stashed",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    else:
        logger.info(
            "interrupt_switch_to_planner_replaced",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    return updates


def _switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
    primary_intent: str | None,
) -> dict[str, Any]:
    if _is_transaction_replacement(
        current_task_types=current_task_types,
        new_task_types=new_task_types,
        primary_intent=primary_intent,
    ):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    if _should_stash_switch(current_task_types, new_task_types):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    return _build_replace_switch_updates(
        state=state,
        interrupt=interrupt,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=planner_output,
    )


def _build_confirmation_reprompt_outbox(
    state: OrchestratorState,
    interrupt: Any,
    task_ids: list[str],
) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    first_task = state.tasks.get(task_ids[0])
    if not first_task:
        return []

    locale = _state_locale(state)
    summary = interrupt.prompt
    if not summary:
        accounts_raw = state.loaded_context.get("accounts") or []
        accounts = [account for account in accounts_raw if isinstance(account, dict)]
        if len(task_ids) == 1:
            summary = build_confirmation_summary(
                task_payload=first_task.payload,
                locale=locale,
                accounts=accounts,
            )
        else:
            parts: list[str] = []
            for task_id in task_ids:
                task = state.tasks.get(task_id)
                if not task:
                    continue
                rendered = build_confirmation_summary(task_payload=task.payload, locale=locale, accounts=accounts)
                if rendered:
                    parts.append(rendered)
            summary = "\n\n".join(parts)
    if not summary:
        return []

    confirmation_payload = first_task.payload.get("confirmation") or {}
    snapshot = confirmation_payload.get("snapshot") or {}
    return [
        {
            "type": "request_confirmation",
            "task_ids": task_ids,
            "summary": summary,
            "snapshot": snapshot,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "actionable_payload": build_actionable_payload(first_task),
        }
    ]


def _auth_header_for_task_ids(state: OrchestratorState, task_ids: list[str], locale: str) -> str:
    task_types = {state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks}
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


def _build_auth_reprompt_outbox(state: OrchestratorState, interrupt: Any) -> list[dict[str, Any]]:
    if not interrupt.task_ids:
        return []
    first_task = state.tasks.get(interrupt.task_ids[0])
    if not first_task:
        return []

    locale = _state_locale(state)
    summary = interrupt.prompt or first_task.payload.get("confirmation", {}).get(
        "summary",
        render_message("orchestrator.execution.pin_prompt_default", locale),
    )
    return [
        {
            "type": "auth_request",
            "method": interrupt.auth_method or "pin",
            "task_ids": interrupt.task_ids,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "header": _auth_header_for_task_ids(state, interrupt.task_ids, locale),
            "summary": summary,
            "actionable_payload": build_actionable_payload(first_task),
        }
    ]


def _reprompt_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    outbox: list[dict[str, Any]] = []
    if interrupt.kind == "input":
        compact_transfer_reprompt = _build_compact_transfer_input_reprompt(state, interrupt)
        if compact_transfer_reprompt:
            logger.info("interrupt_compact_transfer_reprompt", task_ids=interrupt.task_ids)
            outbox = [{"type": "say", "text": compact_transfer_reprompt}]
        elif interrupt.prompt:
            outbox = [{"type": "say", "text": interrupt.prompt}]
    elif interrupt.kind == "confirmation":
        outbox = _build_confirmation_reprompt_outbox(state, interrupt, interrupt.task_ids)
    elif interrupt.kind == "auth":
        outbox = _build_auth_reprompt_outbox(state, interrupt)

    updates: dict[str, Any] = {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
    }
    if outbox:
        updates["outbox"] = outbox
    return updates


def _build_compact_transfer_input_reprompt(state: OrchestratorState, interrupt: Any) -> str | None:
    if getattr(interrupt, "kind", None) != "input":
        return None
    task_ids = getattr(interrupt, "task_ids", None)
    if not isinstance(task_ids, list) or not task_ids:
        return None

    first_task_id = str(task_ids[0])
    task = state.tasks.get(first_task_id)
    if not task or task.type != "transfer":
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return None
    required_fields_raw = fields_by_task.get(first_task_id, [])
    required_fields = [field for field in required_fields_raw if isinstance(field, str)]
    required_set = set(required_fields)
    transfer_fields = {"recipient_account", "recipient_bank_name"}
    if not required_set or not required_set.issubset(transfer_fields):
        return None

    locale = _state_locale(state)
    task_payload = task.payload if isinstance(task.payload, dict) else {}
    recipient_label = format_recipient_display_label(
        task_payload.get("recipient_name"),
        task_payload.get("recipient_resolved_name"),
    )
    safe_recipient = sanitize_recipient_display_name(recipient_label, locale)

    if required_set == transfer_fields:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number_and_bank",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_account"}:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_bank_name"}:
        return cast(str, render_message("response.templates.ask_bank", locale))

    return None


def _format_task_details_for_status(task: TaskSpec, task_type: str) -> str:
    payload = task.payload or {}

    def _fmt_amount(value: Any) -> str | None:
        try:
            return f"₦{float(value):,.0f}"
        except (TypeError, ValueError):
            return None

    if task_type == "transfer":
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name")
        amount = payload.get("amount")
        source_bank = payload.get("source_bank_name")
        parts: list[str] = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if recipient:
            parts.append(f"recipient {recipient}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    if task_type == "airtime":
        phone = payload.get("phone") or payload.get("recipient_phone")
        amount = payload.get("amount")
        parts = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if phone:
            parts.append(f"line {phone}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    if task_type == "data":
        phone = payload.get("phone") or payload.get("recipient_phone")
        plan = payload.get("plan")
        parts = []
        if plan:
            parts.append(f"plan {plan}")
        if phone:
            parts.append(f"line {phone}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    return ""


def _friendly_required_field(field: str) -> str:
    mapping = {
        "recipient_name": "recipient name",
        "recipient_account": "recipient account number",
        "recipient_bank_name": "recipient bank name",
        "beneficiary_id": "beneficiary selection",
        "source_account_id": "source account selection",
        "amount": "amount",
        "pin": "PIN authorization",
        "confirmation_summary": "confirmation",
    }
    return mapping.get(field, field.replace("_", " "))


def _build_requirements_hint(required_fields: list[str], interrupt_kind: str) -> str:
    hints: list[str] = []
    if "beneficiary_id" in required_fields:
        hints.append("Pick a beneficiary option by tapping it or replying with the number.")
    if "source_account_id" in required_fields:
        hints.append("Pick the source account by tapping it or replying with the number.")
    if "amount" in required_fields:
        hints.append("Reply with the amount (for example 5000).")
    if interrupt_kind == "confirmation":
        hints.append("Reply yes to continue or no to cancel.")
    if interrupt_kind == "auth":
        hints.append("Complete PIN authorization to continue.")
    return " ".join(hints)


def _build_status_query_response(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_types: set[str],
    status_query_type: str | None,
) -> str:
    flow_type = next(iter(sorted(task_types))) if task_types else "transaction"
    first_task = state.tasks.get(interrupt.task_ids[0]) if interrupt.task_ids else None
    required_fields = (
        list((interrupt.fields_by_task or {}).get(interrupt.task_ids[0], [])) if interrupt.task_ids else []
    )
    required_fields = [field for field in required_fields if isinstance(field, str)]
    status_kind = status_query_type or "recap"

    stage_text = {
        "input": "waiting for your input",
        "confirmation": "waiting for your confirmation",
        "auth": "waiting for your authorization",
    }.get(interrupt.kind, "in progress")

    if status_kind == "requirements":
        if required_fields:
            needed = ", ".join(_friendly_required_field(field) for field in required_fields)
            hint = _build_requirements_hint(required_fields, interrupt.kind)
            if hint:
                return f"I still need: {needed}. {hint}".strip()
            return f"I still need: {needed}."
        if interrupt.kind == "confirmation":
            return "I need your confirmation to continue. Reply yes to proceed or no to cancel."
        if interrupt.kind == "auth":
            return "I need PIN authorization to continue."
        return "I am waiting for your next input to continue."

    lines = [f"We are in your {flow_type} flow and currently {stage_text}."]
    if first_task:
        detail_line = _format_task_details_for_status(first_task, flow_type)
        if detail_line:
            lines.append(detail_line)
    if required_fields:
        needed = ", ".join(_friendly_required_field(field) for field in required_fields)
        lines.append(f"Next step: provide {needed}.")
    elif interrupt.kind == "confirmation":
        lines.append("Next step: confirm to continue.")
    elif interrupt.kind == "auth":
        lines.append("Next step: complete authorization.")
    return " ".join(lines)


async def _status_query_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    semantic_path_shape: str,
    task_planner: Any,
    text: str,
    active_type: str,
    services: dict[str, Any],
    redis_client: Any | None,
) -> dict[str, Any]:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        logger.info(
            "interrupt_status_query_no_active_flow",
            kind=interrupt.kind,
            status_query_type=route.status_query_type,
            active_types=sorted(current_task_types),
        )
        semantic_route = await _route_interrupt_semantic_turn(
            task_planner=task_planner,
            state=state,
            text=text,
        )
        semantic_decision = str(getattr(semantic_route, "decision", "") or "")
        expected_executors = [
            str(item)
            for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
            if str(item) in TRANSACTION_INTENTS
        ]
        logger.info(
            "interrupt_status_query_semantic_recovery",
            decision=semantic_decision or None,
            mode=getattr(semantic_route, "mode", None),
            target_intent=getattr(semantic_route, "target_intent", None),
            expected_executors=expected_executors,
        )

        if semantic_decision == "cancel":
            return await _cancel_updates(state, interrupt, current_task_types, redis_client)

        if semantic_decision in {"planner_mixed", "planner_ambiguous"}:
            return _build_planner_switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                text=text,
                expected_executors=expected_executors,
            )

        direct_non_transaction_domains = {
            "domain_query": "query",
            "domain_account": "account",
            "domain_support": "support",
            "domain_beneficiary": "beneficiary",
        }
        target_intent = direct_non_transaction_domains.get(semantic_decision)
        if target_intent is not None:
            recovered_route = InterruptRouteDecision(
                decision="switch_intent",
                confidence=getattr(semantic_route, "confidence", 0.0) or 0.0,
                detected_language=getattr(semantic_route, "detected_language", None),
                target_intent=target_intent,
                target_mode="continuation" if getattr(semantic_route, "mode", None) == "continuation" else "new",
                reason="status_query_no_active_flow_semantic_recovery",
            )
            new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
                state=state,
                text=text,
                target_intent=target_intent,
                route=recovered_route,
            )
            return _switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                new_tasks=new_tasks,
                waves=waves,
                new_task_types=new_task_types,
                text=text,
                planner_output=None,
                primary_intent=target_intent,
            )

        direct_transaction_domains = {
            "domain_transfer": "transfer",
            "domain_airtime": "airtime",
            "domain_data": "data",
        }
        target_intent = direct_transaction_domains.get(semantic_decision)
        if target_intent is not None:
            new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
                state=state,
                text=text,
                target_intent=target_intent,
                interrupt=interrupt,
                services=services,
            )
            return _switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                new_tasks=new_tasks,
                waves=waves,
                new_task_types=new_task_types,
                text=text,
                planner_output=None,
                primary_intent=target_intent,
            )

        return _reprompt_updates(state, interrupt)

    response = _build_status_query_response(
        state=state,
        interrupt=interrupt,
        task_types=current_task_types,
        status_query_type=route.status_query_type,
    )
    logger.info(
        "interrupt_status_query_hit",
        kind=interrupt.kind,
        status_query_type=route.status_query_type or "recap",
        active_types=sorted(current_task_types),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": [{"type": "say", "text": response}],
        "semantic_path_shape": semantic_path_shape,
    }


async def _cancel_updates(
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    redis_client: Any | None,
) -> dict[str, Any]:
    del current_task_types
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    return {
        **reset_updates,
        "last_interrupt": interrupt,
        "final_response": cancelled_message(state),
    }


def _normalize_recipient_match_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _digits_only(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


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


def _continue_flow_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    if interrupt.kind in {"input", "confirmation"}:
        task_ids_to_reset = [str(task_id) for task_id in interrupt.task_ids]
        selection_reason = "input_flow"
        matched_task_ids: list[str] = []
        if interrupt.kind == "confirmation":
            task_ids_to_reset, selection_reason, matched_task_ids = _select_confirmation_continue_flow_task_ids(
                state,
                interrupt,
            )
        logger.info(
            "confirmation_update_detected_via_llm",
            tasks=interrupt.task_ids,
            reset_task_ids=task_ids_to_reset,
            selection_reason=selection_reason,
            matched_task_ids=matched_task_ids,
        )
        if interrupt.kind == "confirmation":
            _stash_previous_confirmation_snapshots(state, task_ids_to_reset)
        reset_tasks_to_extracted(
            state.tasks,
            task_ids_to_reset,
            copy_task=True,
            clear_idempotency=True,
        )
        last_interrupt = interrupt
        if interrupt.kind == "confirmation" and task_ids_to_reset != [str(task_id) for task_id in interrupt.task_ids]:
            if hasattr(interrupt, "model_copy"):
                last_interrupt = interrupt.model_copy(update={"task_ids": task_ids_to_reset})
        return {
            "pending_interrupt": None,
            "last_interrupt": last_interrupt,
            "tasks": state.tasks,
        }
    return _reprompt_updates(state, interrupt)


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


def _approve_auth_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    if not state.pin_verified and interrupt.auth_method == "pin":
        logger.info("auth_approval_requires_verified_pin", tasks=interrupt.task_ids)
        return _reprompt_updates(state, interrupt)

    new_tasks = state.tasks.copy()
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        task.stage = TaskStage.EXECUTING
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }


async def _handle_switch_intent_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    task_planner: Any,
    text: str,
    active_type: str,
    current_task_types: set[str],
    services: dict[str, Any],
) -> dict[str, Any]:
    if _is_beneficiary_clarification_interrupt(interrupt):
        logger.info(
            "interrupt_switch_blocked",
            reason="beneficiary_disambiguation_pending",
            target_intent=route.target_intent,
            tasks=interrupt.task_ids,
        )
        return _reprompt_updates(state, interrupt)

    target_intent = (route.target_intent or "").strip().lower()
    if not target_intent or target_intent not in KNOWN_SWITCH_INTENTS:
        logger.info(
            "interrupt_switch_target_unknown",
            target_intent=target_intent or None,
            reason="missing_or_unsupported_target",
        )
        return _reprompt_updates(state, interrupt)

    if target_intent in NON_TRANSACTION_SWITCH_INTENTS:
        new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=target_intent,
            route=route,
        )
    else:
        semantic_route = await _route_interrupt_semantic_turn(
            task_planner=task_planner,
            state=state,
            text=text,
        )

        semantic_decision = str(getattr(semantic_route, "decision", "") or "")
        expected_executors = [
            str(item)
            for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
            if str(item) in TRANSACTION_INTENTS
        ]
        direct_transaction_domains = {
            "domain_transfer": "transfer",
            "domain_airtime": "airtime",
            "domain_data": "data",
        }
        if semantic_decision in {"planner_mixed", "planner_ambiguous"}:
            return _build_planner_switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                text=text,
                expected_executors=expected_executors,
            )

        resolved_target_intent = direct_transaction_domains.get(semantic_decision, target_intent)
        new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=resolved_target_intent,
            interrupt=interrupt,
            services=services,
        )
        target_intent = resolved_target_intent

    return _switch_updates(
        state=state,
        interrupt=interrupt,
        active_type=active_type,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=None,
        primary_intent=target_intent,
    )


async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Process user input against the pending interrupt (if any)."""
    interrupt = state.pending_interrupt
    if not interrupt:
        return {}

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)

    text = state.last_message_text or ""
    current_task_types = _current_task_types(state, interrupt.task_ids)
    active_type = _active_intent(current_task_types)
    task_planner = config["configurable"].get("task_planner")
    redis_client = config["configurable"].get("redis_client")
    services = config["configurable"].get("services") or {}

    if _is_verified_pin_callback(state) and interrupt.kind in {"confirmation", "auth"}:
        flow_matches, callback_flow_type = _callback_flow_matches_interrupt(state, current_task_types)
        if not flow_matches:
            logger.warning(
                "interrupt_callback_flow_mismatch",
                kind=interrupt.kind,
                callback_flow_type=callback_flow_type,
                active_types=sorted(current_task_types),
                tasks=interrupt.task_ids,
            )
            return _reprompt_updates(state, interrupt)
        if interrupt.kind == "confirmation":
            return _approve_confirmation_updates(state, interrupt)
        return _approve_auth_updates(state, interrupt)

    status_shortcut_route = _resolve_deterministic_status_query_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if status_shortcut_route is not None:
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=status_shortcut_route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_deterministic",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    if interrupt.kind == "auth":
        shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
        shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
            text=text,
            interrupt_kind=interrupt.kind,
            locale=shortcut_locale,
        )

        if shortcut_route is not None and shortcut_route.decision == "cancel":
            logger.info(
                "interrupt_cancel_shortcut_disabled",
                kind=interrupt.kind,
                locale=shortcut_locale.value if shortcut_locale else None,
            )
            shortcut_route = None

        if shortcut_route is not None:
            logger.info(
                "interrupt_auth_shortcut_ignored",
                decision=shortcut_route.decision,
                locale=shortcut_locale.value if shortcut_locale else None,
            )
        else:
            logger.info(
                "interrupt_shortcut_miss",
                kind=interrupt.kind,
                locale=shortcut_locale.value if shortcut_locale else None,
                reason=miss_reason,
            )

        route = await _route_interrupt(
            task_planner=task_planner,
            state=state,
            text=text,
            kind=interrupt.kind,
            task_ids=interrupt.task_ids,
            current_task_types=current_task_types,
            fields_by_task=interrupt.fields_by_task,
            prompt=interrupt.prompt,
        )

        if route.decision in {"cancel", "reject_flow"}:
            return await _cancel_updates(state, interrupt, current_task_types, redis_client)

        if route.decision == "switch_intent":
            return await _handle_switch_intent_route(
                state=state,
                interrupt=interrupt,
                route=route,
                task_planner=task_planner,
                text=text,
                active_type=active_type,
                current_task_types=current_task_types,
                services=services,
            )

        # Auth approval is callback-only for PIN. Non-PIN auth (e.g., OTP) can
        # still advance via explicit approve_flow from router classification.
        if route.decision == "approve_flow" and (interrupt.auth_method or "").lower() != "pin":
            return _approve_auth_updates(state, interrupt)

        # For PIN auth, free text cannot advance authorization.
        return _reprompt_updates(state, interrupt)

    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )

    if shortcut_route is not None:
        if shortcut_route.decision == "cancel":
            logger.info(
                "interrupt_cancel_shortcut_disabled",
                kind=interrupt.kind,
                locale=shortcut_locale.value if shortcut_locale else None,
            )
            shortcut_route = None
    if shortcut_route is not None:
        route = shortcut_route
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            status_query_type=route.status_query_type,
            locale=shortcut_locale.value if shortcut_locale else None,
        )
    else:
        logger.info(
            "interrupt_shortcut_miss",
            kind=interrupt.kind,
            locale=shortcut_locale.value if shortcut_locale else None,
            reason=miss_reason,
        )
        route = await _route_interrupt(
            task_planner=task_planner,
            state=state,
            text=text,
            kind=interrupt.kind,
            task_ids=interrupt.task_ids,
            current_task_types=current_task_types,
            fields_by_task=interrupt.fields_by_task,
            prompt=interrupt.prompt,
        )

    if route.decision == "status_query":
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_router_only",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    if route.decision == "cancel":
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if route.decision == "reject_flow":
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if route.decision == "approve_flow":
        if interrupt.kind == "confirmation":
            if not _is_explicit_confirmation_approval_text(state, text):
                logger.info(
                    "confirmation_approve_blocked_non_explicit_text",
                    tasks=interrupt.task_ids,
                )
                return _continue_flow_updates(state, interrupt)
            return _approve_confirmation_updates(state, interrupt)
        if interrupt.kind == "auth":
            return _approve_auth_updates(state, interrupt)
        # Input interrupts cannot be "approved"; keep flow deterministic.
        return _reprompt_updates(state, interrupt)

    if route.decision == "continue_flow":
        return _continue_flow_updates(state, interrupt)

    if route.decision == "switch_intent":
        return await _handle_switch_intent_route(
            state=state,
            interrupt=interrupt,
            route=route,
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
        )

    # "unclear" or any unrecognized decision stays non-destructive.
    return _reprompt_updates(state, interrupt)
