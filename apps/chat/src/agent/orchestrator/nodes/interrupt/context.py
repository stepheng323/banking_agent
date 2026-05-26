import json
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    INTERRUPT_CONTEXT_MAX_CHARS,
    build_interrupt_context_from_summary,
    get_or_build_turn_context_summary,
)
from shared.i18n import LocaleManager
from shared.utils.logging import get_logger

logger = get_logger("apps.chat.src.agent.orchestrator.nodes.interrupt")
TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320

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

def _build_interrupt_context_details(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> tuple[str, str, str]:
    prompt_mode = _select_interrupt_router_prompt_mode(
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
    )
    compact_mode = prompt_mode == "compact"
    summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=state.stashed_query_session if isinstance(state.stashed_query_session, dict) else None,
        query_session_source="stashed" if isinstance(state.stashed_query_session, dict) else None,
        path_label="interrupt_path",
    )
    active_task_state = _build_active_task_router_state(
        state=state,
        task_ids=task_ids,
        compact_mode=compact_mode,
    )
    raw_active_task_state_text = json.dumps(active_task_state, ensure_ascii=True)
    active_task_state_text = _clip_text(
        raw_active_task_state_text,
        INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS,
    )
    raw_required_fields_text = json.dumps(fields_by_task, ensure_ascii=True)
    required_fields_text = _clip_text(
        raw_required_fields_text,
        INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_REQUIRED_FIELDS_MAX_CHARS,
    )
    raw_prompt_text = prompt or ""
    prompt_text = _clip_text(
        raw_prompt_text,
        INTERRUPT_PROMPT_COMPACT_MAX_CHARS if compact_mode else INTERRUPT_PROMPT_MAX_CHARS,
    )
    context = build_interrupt_context_from_summary(
        summary,
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
        active_task_state_json=active_task_state_text,
        required_fields_json=required_fields_text,
        prompt_text=prompt_text,
        prompt_mode=prompt_mode,
    )
    logger.info(
        "interrupt_context_size",
        chars=len(context),
        final_chars=len(context),
        raw_active_task_state_chars=len(raw_active_task_state_text),
        clipped_active_task_state_chars=len(active_task_state_text),
        raw_required_fields_chars=len(raw_required_fields_text),
        clipped_required_fields_chars=len(required_fields_text),
        raw_prompt_chars=len(raw_prompt_text),
        clipped_prompt_chars=len(prompt_text),
        truncated=len(context) >= INTERRUPT_CONTEXT_MAX_CHARS,
        prompt_mode=prompt_mode,
        active_task_state_mode="minimal" if compact_mode else "json",
        task_count=len(task_ids),
        interrupt_kind=kind,
    )
    return context, prompt_mode, ("minimal" if compact_mode else "json")

def _build_interrupt_context(
    *,
    state: OrchestratorState,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> str:
    context, _, _ = _build_interrupt_context_details(
        state=state,
        kind=kind,
        task_ids=task_ids,
        current_task_types=current_task_types,
        fields_by_task=fields_by_task,
        prompt=prompt,
    )
    return context

def _compact_task_payload_for_interrupt_router(payload: dict[str, Any]) -> dict[str, Any]:
    # Keep only stable routing signals to avoid noisy or sensitive prompt context.
    compact: dict[str, Any] = {}
    scalar_fields = (
        "action",
        "amount",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_phone",
        "target_phone",
        "network",
        "is_self",
        "plan_code",
        "plan_name",
        "plan_size_gb",
        "plan_validity_days",
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
                    "target_phone",
                    "network",
                    "is_self",
                    "plan_code",
                    "plan_name",
                    "plan_size_gb",
                    "plan_validity_days",
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

def _minimal_task_payload_for_interrupt_router(payload: dict[str, Any]) -> dict[str, Any]:
    minimal: dict[str, Any] = {}
    for field in (
        "amount",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_phone",
        "target_phone",
        "network",
        "is_self",
        "plan_name",
        "plan_size_gb",
        "plan_validity_days",
        "source_bank_name",
    ):
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            minimal[field] = value
    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict):
        snapshot = confirmation.get("snapshot")
        if isinstance(snapshot, dict):
            summary_view = {
                key: snapshot.get(key)
                for key in (
                    "amount",
                    "recipient_name",
                    "recipient_phone",
                    "target_phone",
                    "network",
                    "is_self",
                    "plan_name",
                    "plan_size_gb",
                    "plan_validity_days",
                    "recipient_bank_name",
                )
                if key in snapshot and isinstance(snapshot.get(key), (str, int, float, bool))
            }
            if summary_view:
                minimal["confirmation"] = summary_view
    if payload.get("recipient_account"):
        minimal["has_recipient_account"] = True
    if payload.get("recipient_bank_name"):
        minimal["has_recipient_bank_name"] = True
    return minimal

def _build_active_task_router_state(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    compact_mode: bool,
) -> dict[str, Any]:
    task_state: dict[str, Any] = {}
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if not task:
            continue
        payload = cast(dict[str, Any], task.payload)
        task_state[task_id] = {
            "type": str(task.type),
            "stage": str(task.stage),
            "payload": (
                _minimal_task_payload_for_interrupt_router(payload)
                if compact_mode
                else _compact_task_payload_for_interrupt_router(payload)
            ),
        }
    return task_state

def _select_interrupt_router_prompt_mode(
    *,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
) -> str:
    if kind == "auth":
        return "compact"
    if len(task_ids) != 1:
        return "full"
    if len(current_task_types) > 1:
        return "full"
    return "compact"

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

def _clear_current_domain_sessions(state: OrchestratorState, domains: set[str]) -> tuple[list[Any], str | None]:
    if not domains:
        stack = list(state.session_stack)
        return stack, (stack[-1].domain if stack else None)

    stack = [session for session in state.session_stack if session.domain not in domains]
    return stack, (stack[-1].domain if stack else None)

def _should_stash_switch(current_task_types: set[str], new_task_types: set[str]) -> bool:
    return (
        bool(current_task_types)
        and current_task_types.issubset(TRANSACTION_INTENTS)
        and not new_task_types.issubset(TRANSACTION_INTENTS)
    )


def _is_transaction_intent(intent: str | None) -> bool:
    return bool(intent and intent in TRANSACTION_INTENTS)


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


async def _cancel_updates(
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
) -> dict[str, Any]:
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    return {
        **reset_updates,
        "last_interrupt": interrupt,
        "final_response": cancelled_message(state),
    }
