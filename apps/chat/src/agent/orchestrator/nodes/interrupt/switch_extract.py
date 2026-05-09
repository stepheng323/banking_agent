import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import _next_interrupt_task_id, _state_locale, logger
from apps.chat.src.agent.orchestrator.nodes.planner.postprocess import (
    _expand_underproduced_transfer_tasks,
    _reconcile_multi_transfer_recipient_tasks,
)
from apps.chat.src.agent.orchestrator.utils.task_payload import build_task_specs_and_waves_from_plan_items
from shared.types.planner import (
    InterruptRouteDecision,
    PlannedTask,
    RecipientAllocation,
    TaskParameters,
)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320
_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

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
    elif not recipient_phone and not recipient_name:
        parameters.is_self = True

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
        planned_tasks, reconcile_meta = _reconcile_multi_transfer_recipient_tasks(planned_tasks, text)
        if reconcile_meta:
            logger.info(
                "interrupt_transfer_multi_recipient_reconcile_applied",
                recipient_count=reconcile_meta["recipient_count"],
                recipient_names=reconcile_meta["recipient_names"],
                changed_tasks=reconcile_meta["changed_tasks"],
            )

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
