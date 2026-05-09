import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancel_match_kind,
    cancel_router_fallback_reason,
    is_obvious_cancel_message,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.auth_resolve import _approve_auth_updates
from apps.chat.src.agent.orchestrator.nodes.interrupt.confirmation_edit import (
    confirmation_scoped_task_removal_ids,
    confirmation_scoped_task_restore_ids,
    remove_confirmation_tasks_and_reconfirm_updates,
    restore_confirmation_tasks_and_reconfirm_updates,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.confirmation_resolve import (
    _amount_patch,
    _approve_confirmation_updates,
    _is_explicit_confirmation_approval_text,
    _message_targets_confirmation_task,
    _resolve_deterministic_confirmation_repeat_route,
    _transfer_task_reference_matches,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import (
    _active_intent,
    _cancel_updates,
    _current_task_types,
    logger,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.input_resolve import (
    _continue_flow_updates,
    _resolve_deterministic_input_selection_route,
    _resolve_deterministic_input_slot_route,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.pending_action_edit import PendingActionEditEngine
from apps.chat.src.agent.orchestrator.nodes.interrupt.reprompt import (
    _reprompt_updates,
    _status_query_updates,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.router import (
    _callback_flow_matches_interrupt,
    _handle_switch_intent_route,
    _is_same_flow_transactional_switch,
    _is_verified_pin_callback,
    _resolve_deterministic_status_query_route,
    _route_interrupt,
    _shortcut_miss_category,
)
from apps.chat.src.agent.orchestrator.services.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from shared.config.settings import settings
from shared.i18n import LocaleManager, render_message
from shared.types.planner import InterruptRouteDecision

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INPUT_INTERRUPT_MAX_ATTEMPTS = 3
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320


async def _remove_or_cancel_confirmation_tasks(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    redis_client: Any | None,
    task_ids_to_remove: list[str],
) -> dict[str, Any]:
    active_task_ids = {
        str(task_id)
        for task_id in getattr(interrupt, "task_ids", [])
        if str(task_id) in state.tasks
    }
    if active_task_ids and set(task_ids_to_remove) >= active_task_ids:
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)
    return remove_confirmation_tasks_and_reconfirm_updates(
        state=state,
        interrupt=interrupt,
        task_ids_to_remove=task_ids_to_remove,
    )


def _narration_patch(value: Any) -> dict[str, Any] | None:
    note = str(value or "").strip(" \t\r\n.,;:!?")
    if not note:
        return None
    return {
        "confirmation": {"confirmed": False},
        "authored_narration": note,
        "narration": note,
        "user_note": note,
    }


def _pending_edit_target_task_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    field: str,
) -> list[str]:
    active_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if not active_task_ids:
        return []

    explicit_ids = [task_id for task_id in decision.target_task_ids if task_id in active_task_ids]
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))

    matched_ids: list[str] = []
    for reference in getattr(decision, "target_texts", []) or []:
        reference_text = str(reference or "").strip()
        if not reference_text:
            continue
        reference_matches = [
            task_id
            for task_id in active_task_ids
            if (task := state.tasks.get(task_id)) is not None
            and (
                _message_targets_confirmation_task(reference_text, task)
                or (task.type == "transfer" and _transfer_task_reference_matches(reference_text, task))
            )
        ]
        if len(reference_matches) == 1:
            matched_ids.extend(reference_matches)

    if matched_ids:
        return list(dict.fromkeys(matched_ids))

    target_types = {str(task_type) for task_type in getattr(decision, "target_types", []) if task_type}
    if target_types:
        type_matches = [
            task_id
            for task_id in active_task_ids
            if (task := state.tasks.get(task_id)) is not None and task.type in target_types
        ]
        if _field_can_apply_collectively(field):
            return [
                task_id
                for task_id in type_matches
                if (task := state.tasks.get(task_id)) is not None
                and _field_applies_to_task(field, task.type)
            ]
        if field in {"source_bank_name", "source_account_index"}:
            return type_matches
        if len(type_matches) == 1:
            return type_matches

    compatible_task_ids = [
        task_id
        for task_id in active_task_ids
        if (task := state.tasks.get(task_id)) is not None
        and _field_applies_to_task(field, task.type)
    ]
    if field in {"source_bank_name", "source_account_index"}:
        return compatible_task_ids
    if len(compatible_task_ids) == 1:
        return compatible_task_ids

    return []


def _field_can_apply_collectively(field: str) -> bool:
    return field in {"amount", "narration", "phone", "network"}


def _field_applies_to_task(field: str, task_type: str) -> bool:
    if field in {"amount", "source_bank_name", "source_account_index"}:
        return task_type in TRANSACTION_INTENTS
    if field in {"narration", "recipient_name", "recipient_account", "recipient_bank_name"}:
        return task_type == "transfer"
    if field in {"phone", "network"}:
        return task_type in {"airtime", "data"}
    return False


def _source_account_patch(*, source_bank_name: Any = None, source_account_index: Any = None) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "confirmation": {"confirmed": False},
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_affinity_mode": None,
        "funding_plan": None,
    }
    if source_bank_name not in (None, ""):
        patch["source_bank_name"] = str(source_bank_name).strip()
        patch["source_account_index"] = None
    if source_account_index not in (None, ""):
        try:
            index = int(source_account_index)
        except (TypeError, ValueError):
            index = 0
        if index > 0:
            patch["source_account_index"] = index
            patch["source_bank_name"] = None
    return patch


def _phone_patch(task_type: str, value: Any) -> dict[str, Any] | None:
    phone = str(value or "").strip()
    if not phone:
        return None
    if task_type == "airtime":
        return {"confirmation": {"confirmed": False}, "recipient_phone": phone, "phone": phone}
    if task_type == "data":
        return {"confirmation": {"confirmed": False}, "target_phone": phone, "phone": phone}
    return None


def _network_patch(value: Any) -> dict[str, Any] | None:
    network = str(value or "").strip()
    if not network:
        return None
    return {"confirmation": {"confirmed": False}, "network": network}


def _recipient_patch(field: str, value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    patch: dict[str, Any] = {
        "confirmation": {"confirmed": False},
        "resolved_from_saved_beneficiary": False,
        "beneficiary_id": None,
        "beneficiary_candidates": [],
    }
    if field == "recipient_name":
        patch.update(
            {
                "recipient_name": text,
                "recipient_resolved_name": None,
                "recipient_account": None,
                "recipient_account_number": None,
                "recipient_bank_name": None,
                "recipient_bank_code": None,
            }
        )
    elif field == "recipient_account":
        patch["recipient_account"] = text
        patch["recipient_account_number"] = text
        patch["recipient_resolved_name"] = None
    elif field == "recipient_bank_name":
        patch["recipient_bank_name"] = text
        patch["bank_name"] = text
        patch["recipient_bank_code"] = None
        patch["recipient_resolved_name"] = None
    else:
        return None
    return patch


def _pending_action_fields(raw_fields: Any) -> dict[str, Any]:
    fields = (
        raw_fields.model_dump(exclude_none=True)
        if hasattr(raw_fields, "model_dump")
        else dict(raw_fields or {})
        if isinstance(raw_fields, dict)
        else {}
    )
    return fields


def _pending_edit_payload_overrides_from_fields(
    *,
    state: OrchestratorState,
    interrupt: Any,
    target: Any,
    fields: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}

    overrides: dict[str, dict[str, Any]] = {}
    for field, value in fields.items():
        if value in (None, ""):
            continue
        target_ids = _pending_edit_target_task_ids(
            state=state,
            interrupt=interrupt,
            decision=target,
            field=str(field),
        )
        for task_id in target_ids:
            task = state.tasks.get(task_id)
            if task is None:
                continue
            patch: dict[str, Any] | None = None
            if field == "narration" and task.type == "transfer":
                patch = _narration_patch(value)
            elif field == "amount" and task.type in TRANSACTION_INTENTS:
                patch = _typed_amount_patch(value)
            elif field in {"recipient_name", "recipient_account", "recipient_bank_name"} and task.type == "transfer":
                patch = _recipient_patch(str(field), value)
            elif field == "source_bank_name" and task.type in TRANSACTION_INTENTS:
                patch = _source_account_patch(source_bank_name=value)
            elif field == "source_account_index" and task.type in TRANSACTION_INTENTS:
                patch = _source_account_patch(source_account_index=value)
            elif field == "phone" and task.type in {"airtime", "data"}:
                patch = _phone_patch(task.type, value)
            elif field == "network" and task.type in {"airtime", "data"}:
                patch = _network_patch(value)
            if patch:
                overrides[task_id] = {**overrides.get(task_id, {}), **patch}

    return overrides


def _pending_edit_payload_overrides_from_decision(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> dict[str, dict[str, Any]]:
    overrides = _pending_edit_payload_overrides_from_fields(
        state=state,
        interrupt=interrupt,
        target=decision,
        fields=_pending_action_fields(getattr(decision, "fields", None)),
    )

    for scoped_update in getattr(decision, "updates", []) or []:
        scoped_overrides = _pending_edit_payload_overrides_from_fields(
            state=state,
            interrupt=interrupt,
            target=scoped_update,
            fields=_pending_action_fields(getattr(scoped_update, "fields", None)),
        )
        for task_id, patch in scoped_overrides.items():
            overrides[task_id] = {**overrides.get(task_id, {}), **patch}

    return overrides


def _typed_amount_patch(value: Any) -> dict[str, Any]:
    amount = 0.0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return {}
    if amount <= 0:
        return {}
    return _amount_patch(amount)


def _pending_edit_has_fields(decision: Any) -> bool:
    fields = _pending_action_fields(getattr(decision, "fields", None))
    if any(value not in (None, "") for value in fields.values()):
        return True
    for scoped_update in getattr(decision, "updates", []) or []:
        scoped_fields = _pending_action_fields(getattr(scoped_update, "fields", None))
        if any(value not in (None, "") for value in scoped_fields.values()):
            return True
    return False


def _confirmation_edit_clarification_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.nodes.interrupt.reprompt import (
        _build_confirmation_scope_clarification_outbox,
    )

    logger.info("pending_action_edit_scope_unresolved", task_ids=getattr(interrupt, "task_ids", None))
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": _build_confirmation_scope_clarification_outbox(state, interrupt),
    }


async def _resolve_semantic_pending_action_edit_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    task_planner: Any,
    active_type: str,
    current_task_types: set[str],
    services: dict[str, Any],
    redis_client: Any | None,
) -> dict[str, Any] | None:
    resolution = await PendingActionEditEngine().interpret(
        state=state,
        interrupt=interrupt,
        text=text,
        task_planner=task_planner,
    )
    if resolution is None:
        return None

    decision = resolution.decision
    logger.info(
        "pending_action_edit_decision",
        operation=decision.operation,
        confidence=decision.confidence,
        target_task_ids=decision.target_task_ids,
        target_types=decision.target_types,
        target_texts=decision.target_texts,
    )

    if decision.operation == "remove_tasks":
        task_ids = confirmation_scoped_task_removal_ids(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )
        if task_ids:
            return await _remove_or_cancel_confirmation_tasks(
                state=state,
                interrupt=interrupt,
                current_task_types=current_task_types,
                redis_client=redis_client,
                task_ids_to_remove=task_ids,
            )

    if decision.operation == "restore_tasks":
        task_ids = confirmation_scoped_task_restore_ids(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )
        if task_ids:
            return restore_confirmation_tasks_and_reconfirm_updates(
                state=state,
                interrupt=interrupt,
                task_ids_to_restore=task_ids,
            )

    if decision.operation == "update_fields":
        overrides = _pending_edit_payload_overrides_from_decision(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )
        if overrides:
            return _continue_flow_updates(
                state,
                interrupt,
                precomputed_payload_overrides=overrides,
            )
        if _pending_edit_has_fields(decision):
            return _confirmation_edit_clarification_updates(state, interrupt)
        return None

    if decision.operation == "add_tasks":
        target_types = [
            str(task_type).strip().lower()
            for task_type in (decision.target_types or [])
            if str(task_type).strip().lower() in TRANSACTION_INTENTS
        ]
        # For add-task edits, the concrete transaction type is the safest signal.
        # Some models populate target_intent even though the operation is add_tasks;
        # prefer the explicit target_types slot when it is singular.
        target_intent = (
            target_types[0]
            if len(set(target_types)) == 1
            else (decision.target_intent or "").strip().lower()
        )
        if target_intent in TRANSACTION_INTENTS:
            route = InterruptRouteDecision(
                decision="switch_intent",
                confidence=decision.confidence,
                detected_language=decision.detected_language,
                target_intent=target_intent,
                target_mode="continuation",
                reason=decision.reason or "pending_action_add_task",
            )
            return await _handle_switch_intent_route(
                state=state,
                interrupt=interrupt,
                route=route,
                task_planner=task_planner,
                text=decision.add_instruction or text,
                active_type=active_type,
                current_task_types=current_task_types,
                services=services,
            )

    if decision.operation == "cancel_all":
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if decision.operation == "status_query":
        route = InterruptRouteDecision(
            decision="status_query",
            confidence=decision.confidence,
            detected_language=decision.detected_language,
            status_query_type=decision.status_query_type or "recap",
            reason=decision.reason or "pending_action_status_query",
        )
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            semantic_path_shape="pending_action_edit",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    if decision.operation == "switch_intent":
        target_intent = str(decision.target_intent or "").strip().lower()
        if target_intent in KNOWN_SWITCH_INTENTS:
            route = InterruptRouteDecision(
                decision="switch_intent",
                confidence=decision.confidence,
                detected_language=decision.detected_language,
                target_intent=target_intent,
                target_mode="continuation" if target_intent in TRANSACTION_INTENTS else "new",
                reason=decision.reason or "pending_action_switch_intent",
            )
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

    return None


def _pending_transaction_interrupt_expiry(interrupt: Any, current_task_types: set[str]) -> float | None:
    if not current_task_types.intersection(TRANSACTION_INTENTS):
        return None
    ttl_seconds = max(int(getattr(settings, "pending_transaction_ttl", 0) or 0), 0)
    if ttl_seconds <= 0:
        return None
    explicit_expiry = getattr(interrupt, "expires_at_ts", None)
    if isinstance(explicit_expiry, (int, float)) and explicit_expiry > 0:
        return float(explicit_expiry)
    created_at = getattr(interrupt, "created_at_ts", None)
    if not isinstance(created_at, (int, float)) or created_at <= 0:
        return None
    return float(created_at) + ttl_seconds


async def _expired_transaction_interrupt_updates(
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    redis_client: Any | None,
) -> dict[str, Any] | None:
    expires_at = _pending_transaction_interrupt_expiry(interrupt, current_task_types)
    if expires_at is None or time.time() <= expires_at:
        return None

    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    logger.info(
        "pending_transaction_interrupt_expired",
        kind=getattr(interrupt, "kind", None),
        task_ids=getattr(interrupt, "task_ids", None),
        expires_at=expires_at,
    )
    return {
        **reset_updates,
        "last_interrupt": interrupt,
    }


async def _reprompt_or_reset_updates(
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
) -> dict[str, Any]:
    if getattr(interrupt, "kind", None) != "input":
        return _reprompt_updates(state, interrupt)

    next_attempts = max(int(getattr(interrupt, "attempts", 0) or 0) + 1, 1)
    if next_attempts < INPUT_INTERRUPT_MAX_ATTEMPTS:
        return _reprompt_updates(state, interrupt.model_copy(update={"attempts": next_attempts}))

    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    response_text = render_message("orchestrator.execution.input_attempts_exhausted", locale)
    logger.info(
        "interrupt_input_attempt_budget_exhausted",
        task_ids=getattr(interrupt, "task_ids", None),
        attempts=next_attempts,
    )
    return {
        **reset_updates,
        "last_interrupt": interrupt,
        "outbox": [{"type": "say", "text": response_text}],
        "final_response": response_text,
    }







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

    expired_updates = await _expired_transaction_interrupt_updates(
        state=state,
        interrupt=interrupt,
        current_task_types=current_task_types,
        redis_client=redis_client,
    )
    if expired_updates is not None:
        return expired_updates

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
            return await _reprompt_or_reset_updates(state, interrupt, redis_client)
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

    input_shortcut_route = _resolve_deterministic_input_selection_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if input_shortcut_route is not None:
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=input_shortcut_route.decision,
            reason=input_shortcut_route.reason,
        )
        return _continue_flow_updates(state, interrupt)

    input_slot_shortcut_route = _resolve_deterministic_input_slot_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if input_slot_shortcut_route is not None:
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=input_slot_shortcut_route.decision,
            reason=input_slot_shortcut_route.reason,
        )
        return _continue_flow_updates(state, interrupt)

    repeat_shortcut_route = _resolve_deterministic_confirmation_repeat_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if repeat_shortcut_route is not None:
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=repeat_shortcut_route.decision,
            reason=repeat_shortcut_route.reason,
            status_query_type=repeat_shortcut_route.status_query_type,
            locale=resolve_shortcut_locale((state.loaded_context or {}).get("language")).value
            if resolve_shortcut_locale((state.loaded_context or {}).get("language"))
            else None,
        )
        return _continue_flow_updates(state, interrupt)

    semantic_edit_updates = await _resolve_semantic_pending_action_edit_updates(
        state=state,
        interrupt=interrupt,
        text=text,
        task_planner=task_planner,
        active_type=active_type,
        current_task_types=current_task_types,
        services=services,
        redis_client=redis_client,
    )
    if semantic_edit_updates is not None:
        return semantic_edit_updates

    deterministic_cancel_kind = cancel_match_kind(text) if is_obvious_cancel_message(text) else None
    if deterministic_cancel_kind is not None:
        logger.info(
            "interrupt_deterministic_cancel_hit",
            kind=interrupt.kind,
            match_kind=deterministic_cancel_kind,
        )
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if interrupt.kind == "auth":
        shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
        shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
            text=text,
            interrupt_kind=interrupt.kind,
            locale=shortcut_locale,
        )

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
                miss_category=_shortcut_miss_category(miss_reason),
            )
            logger.info(
                "interrupt_cancel_router_fallback",
                kind=interrupt.kind,
                reason=cancel_router_fallback_reason(text),
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
            if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
                logger.info(
                    "interrupt_same_flow_switch_shortcut",
                    kind=interrupt.kind,
                    active_type=active_type,
                    target_intent=route.target_intent,
                )
                return _continue_flow_updates(state, interrupt)
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
        return await _reprompt_or_reset_updates(state, interrupt, redis_client)

    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )

    if shortcut_route is not None:
        route = shortcut_route
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            reason=route.reason,
            status_query_type=route.status_query_type,
            locale=shortcut_locale.value if shortcut_locale else None,
        )
    else:
        logger.info(
            "interrupt_shortcut_miss",
            kind=interrupt.kind,
            locale=shortcut_locale.value if shortcut_locale else None,
            reason=miss_reason,
            miss_category=_shortcut_miss_category(miss_reason),
        )
        logger.info(
            "interrupt_cancel_router_fallback",
            kind=interrupt.kind,
            reason=cancel_router_fallback_reason(text),
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
        return await _reprompt_or_reset_updates(state, interrupt, redis_client)

    if route.decision == "continue_flow":
        return _continue_flow_updates(state, interrupt)

    if route.decision == "switch_intent":
        if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
            logger.info(
                "interrupt_same_flow_switch_shortcut",
                kind=interrupt.kind,
                active_type=active_type,
                target_intent=route.target_intent,
            )
            return _continue_flow_updates(state, interrupt)
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
    return await _reprompt_or_reset_updates(state, interrupt, redis_client)
