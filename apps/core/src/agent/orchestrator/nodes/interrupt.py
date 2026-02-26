import json
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import _build_user_state_summary
from apps.core.src.agent.orchestrator.services.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from apps.core.src.agent.orchestrator.utils.task_payload import build_task_spec_from_plan_item
from apps.core.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted, set_tasks_cancelled
from apps.core.src.agent.orchestrator.utils.waves import build_dependency_waves
from shared.formatters.confirmation import build_confirmation_summary
from shared.formatters.prompts import format_auth_reason
from shared.i18n import LocaleManager, render_message
from shared.types.planner import InterruptRouteDecision, PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
DIRECT_SWITCH_INTENTS = {"query", "account", "faq", "support"}
PLANNER_SWITCH_INTENTS = {
    "transfer",
    "airtime",
    "data",
    "beneficiary",
    "mixed",
    "conversational",
    "cancel",
}
INTERRUPT_CONTEXT_MAX_CHARS = 1800
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300


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


def _build_interrupt_context(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> str:
    required_fields_text = _clip_text(
        json.dumps(fields_by_task, ensure_ascii=True),
        INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
    )
    prompt_text = _clip_text(prompt or "", INTERRUPT_PROMPT_MAX_CHARS)
    parts = [
        f"Active Flow: {kind} required for tasks {task_ids} "
        f"(types: {', '.join(sorted(current_task_types))}).\n"
        f"required_fields={required_fields_text}\n"
        f"prompt={prompt_text}"
    ]
    user_state = _build_user_state_summary(state)
    if user_state:
        parts.append(user_state)
    context_raw = "\n\n".join(parts)
    context = _clip_text(context_raw, INTERRUPT_CONTEXT_MAX_CHARS)
    logger.info("interrupt_context_size", chars=len(context), truncated=context != context_raw)
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
        route = await task_planner.route_pending_input(state.phone_number, text, context=route_context)
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


def _clear_current_domain_sessions(state: OrchestratorState, domains: set[str]) -> tuple[list[Any], str | None]:
    if not domains:
        stack = list(state.session_stack)
        return stack, (stack[-1].domain if stack else None)

    stack = [session for session in state.session_stack if session.domain not in domains]
    return stack, (stack[-1].domain if stack else None)


def _build_waves_from_tasks(tasks: dict[str, TaskSpec]) -> list[list[str]]:
    task_ids = list(tasks.keys())
    depends_on_by_task = {task_id: list(tasks[task_id].depends_on) for task_id in task_ids}
    return cast(list[list[str]], build_dependency_waves(task_ids, depends_on_by_task))


def _build_replanned_tasks(
    planner_output: PlannerOutput,
    text: str,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    new_tasks: dict[str, TaskSpec] = {}
    for plan_item in planner_output.tasks:
        spec = build_task_spec_from_plan_item(
            plan_item,
            text,
            preserve_existing_action_instruction=True,
            include_skip_extraction=True,
            strip_transfer_recipient_suffix=True,
            format_narration_requires_recipient_field=False,
        )
        new_tasks[spec.id] = spec

    waves = _build_waves_from_tasks(new_tasks)
    new_task_types = {str(task.type) for task in new_tasks.values()}
    return new_tasks, waves, new_task_types


def _build_direct_switch_tasks(
    *,
    state: OrchestratorState,
    text: str,
    target_intent: str,
    route: InterruptRouteDecision,
) -> tuple[dict[str, TaskSpec], list[list[str]], set[str]]:
    base_id = f"interrupt_{target_intent}_1"
    task_id = base_id
    index = 1
    while task_id in state.tasks:
        index += 1
        task_id = f"interrupt_{target_intent}_{index}"

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
        return _build_transaction_replacement_updates(
            state=state,
            interrupt=interrupt,
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
        summary = build_confirmation_summary(
            task_payload=first_task.payload,
            locale=locale,
            accounts=accounts,
        )
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
        }
    ]


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
            "header": format_auth_reason(first_task.type, locale=locale),
            "summary": summary,
        }
    ]


def _reprompt_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    outbox: list[dict[str, Any]] = []
    if interrupt.kind == "input":
        if interrupt.prompt:
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


def _status_query_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
) -> dict[str, Any]:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        logger.info(
            "interrupt_status_query_no_active_flow",
            kind=interrupt.kind,
            status_query_type=route.status_query_type,
            active_types=sorted(current_task_types),
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
    }


def _cancel_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    set_tasks_cancelled(state.tasks, interrupt.task_ids, copy_task=True)
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
    }


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


async def _switch_via_planner(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_planner: Any,
    text: str,
    active_type: str,
    current_task_types: set[str],
) -> dict[str, Any]:
    context_summary = _build_interrupt_context(
        state=state,
        kind=interrupt.kind,
        task_ids=interrupt.task_ids,
        current_task_types=current_task_types,
        fields_by_task=interrupt.fields_by_task,
        prompt=interrupt.prompt,
    )
    try:
        planner_output = await task_planner.plan_tasks(state.phone_number, text, context=context_summary)
    except Exception as exc:
        logger.warning("interrupt_switch_planner_failed", kind=interrupt.kind, error=str(exc))
        return _reprompt_updates(state, interrupt)

    if getattr(planner_output, "is_cancellation", False):
        return _cancel_updates(state, interrupt)

    if not planner_output.tasks:
        locale = _state_locale(state)
        response_key = getattr(planner_output, "response_key", None)
        if response_key:
            final_response = render_message(response_key, locale)
        else:
            final_response = planner_output.response or render_message("conversational.clarify", locale)
        return {
            "pending_interrupt": None,
            "last_interrupt": interrupt,
            "waves": [],
            "final_response": final_response,
            "planner_output": planner_output,
        }

    new_tasks, waves, new_task_types = _build_replanned_tasks(planner_output, text)
    return _switch_updates(
        state=state,
        interrupt=interrupt,
        active_type=active_type,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=planner_output,
        primary_intent=planner_output.primary_intent,
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
        return _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
        )

    if route.decision == "cancel":
        return _cancel_updates(state, interrupt)

    if route.decision == "reject_flow":
        return _cancel_updates(state, interrupt)

    if route.decision == "approve_flow":
        if interrupt.kind == "confirmation":
            return _approve_confirmation_updates(state, interrupt)
        if interrupt.kind == "auth":
            return _approve_auth_updates(state, interrupt)
        # Input interrupts cannot be "approved"; keep flow deterministic.
        return _reprompt_updates(state, interrupt)

    if route.decision == "continue_flow":
        if interrupt.kind == "input":
            reset_tasks_to_extracted(
                state.tasks,
                interrupt.task_ids,
                copy_task=True,
                clear_idempotency=True,
            )
            return {
                "pending_interrupt": None,
                "last_interrupt": interrupt,
                "tasks": state.tasks,
            }
        return _reprompt_updates(state, interrupt)

    if route.decision == "switch_intent" and _is_beneficiary_clarification_interrupt(interrupt):
        logger.info(
            "interrupt_switch_blocked",
            reason="beneficiary_disambiguation_pending",
            target_intent=route.target_intent,
            tasks=interrupt.task_ids,
        )
        return _reprompt_updates(state, interrupt)

    if route.decision == "switch_intent":
        target_intent = (route.target_intent or "").strip().lower()
        if target_intent in DIRECT_SWITCH_INTENTS:
            new_tasks, waves, new_task_types = _build_direct_switch_tasks(
                state=state,
                text=text,
                target_intent=target_intent,
                route=route,
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

        if target_intent in PLANNER_SWITCH_INTENTS or not target_intent:
            return await _switch_via_planner(
                state=state,
                interrupt=interrupt,
                task_planner=task_planner,
                text=text,
                active_type=active_type,
                current_task_types=current_task_types,
            )

        # Unknown targets are planner-mediated for safety.
        return await _switch_via_planner(
            state=state,
            interrupt=interrupt,
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            current_task_types=current_task_types,
        )

    # "unclear" or any unrecognized decision stays non-destructive.
    return _reprompt_updates(state, interrupt)
