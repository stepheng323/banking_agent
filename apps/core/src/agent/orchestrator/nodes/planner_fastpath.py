"""Context read fastpath planner helpers."""

import time
from typing import Any, cast

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.types.planner import PlannedTask, TaskParameters
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CONTEXT_READ_FASTPATH_LIST_LIMIT = 5
CONTEXT_FASTPATH_ACCOUNT_SUBTYPES = {
    "account_count",
    "linked_accounts_summary",
    "default_account_identity",
    "pending_mandate_explanation",
    "account_mandate_readiness_summary",
    "account_linked_bank_existence_check",
}
CONTEXT_FASTPATH_BENEFICIARY_SUBTYPES = {
    "beneficiary_count",
    "beneficiary_list",
    "beneficiary_existence_check",
    "beneficiary_name_match_preview",
}
CONTEXT_FASTPATH_FLOW_SUBTYPES = {
    "flow_recap",
    "flow_missing_requirements",
}
CONTEXT_FASTPATH_SUBTYPES = (
    CONTEXT_FASTPATH_ACCOUNT_SUBTYPES | CONTEXT_FASTPATH_BENEFICIARY_SUBTYPES | CONTEXT_FASTPATH_FLOW_SUBTYPES
)
TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
BENEFICIARY_MATCH_PREVIEW_LIMIT = 3
BENEFICIARY_FASTPATH_PERSIST_SUBTYPES = {"beneficiary_list", "beneficiary_name_match_preview"}
NO_ACTIVE_FLOW_FASTPATH_MESSAGE = "There is no active transfer flow right now. Start a transfer and I will guide you."


def _infer_recent_domain_focus(state: OrchestratorState) -> str | None:
    """Infer the most recent domain focus from prior planner output/state."""
    prior_output = state.planner_output
    if prior_output:
        prior_subtype = _planner_fastpath_subtype(prior_output)
        if prior_subtype in CONTEXT_FASTPATH_ACCOUNT_SUBTYPES:
            return "account"
        if prior_subtype in CONTEXT_FASTPATH_BENEFICIARY_SUBTYPES:
            return "beneficiary"
        if prior_subtype in CONTEXT_FASTPATH_FLOW_SUBTYPES:
            return "orchestrator"

        prior_tasks = getattr(prior_output, "tasks", None) or []
        prior_executors = {getattr(task, "executor", None) for task in prior_tasks if getattr(task, "executor", None)}
        if len(prior_executors) == 1:
            return cast(str, next(iter(prior_executors)))

    if state.waves and state.current_wave_index < len(state.waves):
        wave = state.waves[state.current_wave_index]
        if wave:
            task = state.tasks.get(wave[0])
            if task and task.type:
                return task.type

    return None


def _planner_fastpath_subtype(planner_output: Any) -> str | None:
    """Read planner-provided fastpath subtype when it is recognized."""
    subtype = getattr(planner_output, "context_fastpath_subtype", None)
    if isinstance(subtype, str) and subtype in CONTEXT_FASTPATH_SUBTYPES:
        return subtype
    return None


def _has_context_for_fastpath_subtype(state: OrchestratorState, subtype: str) -> bool:
    """Check whether current loaded context is sufficient for fastpath answer."""
    ctx = state.loaded_context or {}
    accounts_raw = ctx.get("accounts")
    beneficiaries_raw = ctx.get("beneficiaries")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []

    if subtype in {"account_count", "linked_accounts_summary", "account_mandate_readiness_summary"}:
        return isinstance(accounts_raw, list)
    if subtype == "account_linked_bank_existence_check":
        return isinstance(accounts_raw, list) and bool(accounts)
    if subtype == "default_account_identity":
        return isinstance(accounts_raw, list) and any(bool(acc.get("is_default")) for acc in accounts)
    if subtype == "pending_mandate_explanation":
        return isinstance(accounts_raw, list) and any(acc.get("mandate_status") == "pending" for acc in accounts)
    if subtype in CONTEXT_FASTPATH_BENEFICIARY_SUBTYPES:
        return isinstance(beneficiaries_raw, list)
    if subtype in CONTEXT_FASTPATH_FLOW_SUBTYPES:
        pending_interrupt = state.pending_interrupt
        if not pending_interrupt or not pending_interrupt.task_ids:
            return False
        task_types = {
            state.tasks[tid].type for tid in pending_interrupt.task_ids if tid in state.tasks and state.tasks[tid]
        }
        return bool(task_types) and task_types.issubset(TRANSACTION_EXECUTORS)
    return False


def _context_fastpath_total_items(state: OrchestratorState, subtype: str) -> int | None:
    """Return total list size for list-style fastpath requests."""
    ctx = state.loaded_context or {}
    if subtype == "linked_accounts_summary":
        accounts = ctx.get("accounts")
        return len(accounts) if isinstance(accounts, list) else None
    if subtype == "beneficiary_list":
        beneficiaries = ctx.get("beneficiaries")
        return len(beneficiaries) if isinstance(beneficiaries, list) else None
    if subtype == "beneficiary_name_match_preview":
        beneficiaries = ctx.get("beneficiaries")
        return len(beneficiaries) if isinstance(beneficiaries, list) else None
    return None


def _context_fastpath_shown_limit(subtype: str) -> int:
    if subtype == "beneficiary_name_match_preview":
        return BENEFICIARY_MATCH_PREVIEW_LIMIT
    return CONTEXT_READ_FASTPATH_LIST_LIMIT


def _build_beneficiary_fastpath_context_updates(
    state: OrchestratorState,
    planner_output: Any,
    subtype: str | None,
) -> dict[str, Any]:
    """Persist beneficiary fastpath entities as context frames for pronoun follow-ups."""
    if subtype not in BENEFICIARY_FASTPATH_PERSIST_SUBTYPES:
        return {}
    if (
        not planner_output
        or planner_output.primary_intent != "conversational"
        or getattr(planner_output, "tasks", None)
    ):
        return {}
    if not _has_context_for_fastpath_subtype(state, subtype):
        return {}

    raw_beneficiaries = (state.loaded_context or {}).get("beneficiaries")
    if not isinstance(raw_beneficiaries, list):
        return {}

    entities: list[ContextEntity] = []
    for item in raw_beneficiaries:
        if not isinstance(item, dict):
            continue
        label = str(item.get("alias") or item.get("account_name") or item.get("name") or "Beneficiary").strip()
        beneficiary_id = item.get("id")
        entity_id = str(beneficiary_id).strip() if beneficiary_id is not None else None
        data = {
            "id": entity_id,
            "alias": item.get("alias"),
            "account_name": item.get("account_name"),
            "account_number": item.get("account_number"),
            "bank_name": item.get("bank_name"),
            "bank_code": item.get("bank_code"),
            "beneficiary_type": item.get("beneficiary_type"),
        }
        entities.append(ContextEntity(entity_type=EntityType.BENEFICIARY, entity_id=entity_id, label=label, data=data))

    if not entities:
        return {}

    frame = ContextFrame(
        frame_id=f"planner_beneficiaries_{int(time.time())}",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=entities,
        created_at_ts=int(time.time()),
        source_message_id=state.last_message_id,
    )
    OrchestratorContextManager().push_frame(state, frame)
    logger.info("planner_fastpath_context_frame_pushed", subtype=subtype, count=len(entities))
    return {"context_frames": state.context_frames}


def _build_fastpath_fallback_task(
    subtype: str,
    message_text: str,
    *,
    account_action_override: str | None = None,
) -> PlannedTask | None:
    """Build a read-only worker task when fastpath should not answer directly."""
    if subtype in CONTEXT_FASTPATH_ACCOUNT_SUBTYPES:
        action = account_action_override or "list_accounts"
        if action == "list":
            action = "list_accounts"
        risk = "READ_ONLY"
        if action in {"link", "unlink", "set_default"}:
            risk = "MUTATION"
        return PlannedTask(
            task_id="t1",
            action=action,
            executor="account",
            instruction=message_text,
            parameters=TaskParameters(),
            risk=risk,
        )

    if subtype in CONTEXT_FASTPATH_BENEFICIARY_SUBTYPES:
        return PlannedTask(
            task_id="t1",
            action="list_beneficiaries",
            executor="beneficiary",
            instruction=message_text,
            parameters=TaskParameters(),
            risk="READ_ONLY",
        )

    return None


__all__ = [
    "NO_ACTIVE_FLOW_FASTPATH_MESSAGE",
    "TRANSACTION_EXECUTORS",
    "_build_beneficiary_fastpath_context_updates",
    "_build_fastpath_fallback_task",
    "_context_fastpath_shown_limit",
    "_context_fastpath_total_items",
    "_has_context_for_fastpath_subtype",
    "_infer_recent_domain_focus",
    "_planner_fastpath_subtype",
]
