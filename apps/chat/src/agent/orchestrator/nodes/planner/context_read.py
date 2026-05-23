"""Planner-owned context-read helpers."""

import re
import time
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.i18n import render_message
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.types.planner import PlannedTask, TaskParameters
from shared.utils.bank_aliases import BANK_ALIASES, get_bank_search_terms, normalize_bank_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CONTEXT_READ_LIST_LIMIT = 5
CONTEXT_READ_ACCOUNT_SUBTYPES = {
    "account_count",
    "linked_accounts_summary",
    "default_account_identity",
    "pending_mandate_explanation",
    "account_mandate_readiness_summary",
    "account_linked_bank_existence_check",
}
CONTEXT_READ_BENEFICIARY_SUBTYPES = {
    "beneficiary_count",
    "beneficiary_list",
    "beneficiary_existence_check",
    "beneficiary_name_match_preview",
}
CONTEXT_READ_FLOW_SUBTYPES = {
    "flow_recap",
    "flow_missing_requirements",
}
CONTEXT_READ_SUBTYPES = (
    CONTEXT_READ_ACCOUNT_SUBTYPES | CONTEXT_READ_BENEFICIARY_SUBTYPES | CONTEXT_READ_FLOW_SUBTYPES
)
TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
BENEFICIARY_MATCH_PREVIEW_LIMIT = 3
BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES = {"beneficiary_list", "beneficiary_name_match_preview"}
NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE = (
    "There is no active transfer flow right now. Start a transfer and I will guide you."
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _infer_recent_domain_focus(state: OrchestratorState) -> str | None:
    """Infer the most recent domain focus from prior planner output/state."""
    prior_output = state.planner_output
    if prior_output:
        prior_subtype = _planner_context_read_subtype(prior_output)
        if prior_subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
            return "account"
        if prior_subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
            return "beneficiary"
        if prior_subtype in CONTEXT_READ_FLOW_SUBTYPES:
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


def _planner_context_read_subtype(planner_output: Any) -> str | None:
    """Read planner-provided context-read subtype when it is recognized."""
    subtype = getattr(planner_output, "context_read_subtype", None)
    if isinstance(subtype, str) and subtype in CONTEXT_READ_SUBTYPES:
        return subtype
    return None


def _compact_token(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.lower())


def _find_account_for_bank_followup(text: str, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_text = _compact_token(text)
    if not compact_text:
        return None

    for account in accounts:
        bank_name = str(account.get("bank_name") or "").strip()
        if not bank_name:
            continue
        search_terms = {normalize_bank_name(bank_name), *get_bank_search_terms(bank_name)}
        if any(term and _compact_token(term) in compact_text for term in search_terms):
            return account
    return None


def _extract_requested_bank_label(text: str) -> str | None:
    lowered = text.lower()
    for alias in sorted(BANK_ALIASES.keys(), key=len, reverse=True):
        if alias in lowered:
            return alias.title()
    return None


def synthesize_account_context_read_response(
    state: OrchestratorState,
    subtype: str,
    text: str,
    locale: str,
) -> str | None:
    """Build deterministic account context-read responses from loaded context when beneficial."""
    accounts_raw = (state.loaded_context or {}).get("accounts")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []
    if subtype != "account_linked_bank_existence_check" or not accounts:
        return None

    match = _find_account_for_bank_followup(text, [a for a in accounts if isinstance(a, dict)])
    if match is None:
        bank_label = _extract_requested_bank_label(text)
        if bank_label:
            return f"No, you do not have {bank_label} linked."
        return None

    bank_name = str(match.get("bank_name") or render_message("mandate.bank_fallback", locale))
    status = str(match.get("mandate_status") or "").strip().lower()
    if status == "ready":
        return f"Yes, you have {bank_name} linked and ready."

    pending_message = build_pending_mandate_message([match], locale)
    return f"Yes, you have {bank_name} linked, but it is not ready for payments yet.\n\n{pending_message}"


def _has_context_for_read_subtype(state: OrchestratorState, subtype: str) -> bool:
    """Check whether current loaded context is sufficient for a context-read answer."""
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
    if subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
        return isinstance(beneficiaries_raw, list)
    if subtype in CONTEXT_READ_FLOW_SUBTYPES:
        pending_interrupt = state.pending_interrupt
        if not pending_interrupt or not pending_interrupt.task_ids:
            return False
        task_types = {
            state.tasks[tid].type for tid in pending_interrupt.task_ids if tid in state.tasks and state.tasks[tid]
        }
        return bool(task_types) and task_types.issubset(TRANSACTION_EXECUTORS)
    return False


def _context_read_total_items(state: OrchestratorState, subtype: str) -> int | None:
    """Return total list size for list-style context-read requests."""
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


def _context_read_shown_limit(subtype: str) -> int:
    if subtype == "beneficiary_name_match_preview":
        return BENEFICIARY_MATCH_PREVIEW_LIMIT
    return CONTEXT_READ_LIST_LIMIT


def _build_beneficiary_context_read_updates(
    state: OrchestratorState,
    planner_output: Any,
    subtype: str | None,
) -> dict[str, Any]:
    """Persist beneficiary context-read entities as context frames for pronoun follow-ups."""
    if subtype not in BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES:
        return {}
    if (
        not planner_output
        or planner_output.primary_intent != "conversational"
        or getattr(planner_output, "tasks", None)
    ):
        return {}
    if not _has_context_for_read_subtype(state, subtype):
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
    logger.info("planner_context_read_frame_pushed", subtype=subtype, count=len(entities))
    return {"context_frames": state.context_frames, "referent_memory": state.referent_memory}


def _build_context_read_fallback_task(
    subtype: str,
    message_text: str,
    *,
    account_action_override: str | None = None,
) -> PlannedTask | None:
    """Build a read-only worker task when context-read should not answer directly."""
    if subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
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

    if subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
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
    "NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE",
    "TRANSACTION_EXECUTORS",
    "_build_beneficiary_context_read_updates",
    "_build_context_read_fallback_task",
    "_context_read_shown_limit",
    "_context_read_total_items",
    "_has_context_for_read_subtype",
    "_infer_recent_domain_focus",
    "_planner_context_read_subtype",
    "synthesize_account_context_read_response",
]
