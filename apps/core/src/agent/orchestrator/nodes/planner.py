import re
import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.core.src.agent.orchestrator.models.domain import MetaIntent, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT as _CONTEXT_ACCOUNT_PREVIEW_LIMIT,
)
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
    _clip_text,
    _compact_payload_for_prompt,
)
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.core.src.agent.orchestrator.utils.task_payload import (
    _derive_recipients_from_user_text,
    build_task_spec_from_plan_item,
)
from apps.core.src.agent.orchestrator.utils.waves import build_dependency_waves
from shared.i18n import (
    LanguageDetectionSignal,
    LocaleManager,
    MessageKey,
    render_message,
    render_policy_notice,
    render_safe_capability_fallback,
    render_text,
)
from shared.policy.loader import get_cached_policy
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.types.planner import PlannedTask, TaskParameters
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SAFE_CAPABILITY_FALLBACK = render_safe_capability_fallback("en")


SUPPORTED_EXECUTOR_LABELS = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "query": "transaction query",
    "account": "account actions",
    "support": "support request",
    "faq": "banking help",
    "beneficiary": "beneficiary management",
}

CONTEXT_ACCOUNT_PREVIEW_LIMIT = _CONTEXT_ACCOUNT_PREVIEW_LIMIT
CONTEXT_READ_FASTPATH_LIST_LIMIT = 5
PLANNER_CONTEXT_BENEFICIARY_SUGGESTION_MAX_CHARS = 420
PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS = 640
PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS = 700
PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS = 700
PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS = 220
PLANNER_CONTEXT_USER_STATE_MAX_CHARS = 900
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
QUOTED_REPLAY_MIN_CONFIDENCE = 0.75
NO_ACTIVE_FLOW_FASTPATH_MESSAGE = "There is no active transfer flow right now. Start a transfer and I will guide you."
META_RESPONSE_KEY_TO_INTENT: dict[str, MetaIntent] = {
    "conversational.identity": MetaIntent.IDENTITY,
    "conversational.brand_origin": MetaIntent.BRAND_ORIGIN,
    "conversational.capability_question": MetaIntent.CAPABILITIES,
    "conversational.out_of_scope": MetaIntent.LIMITS,
}
QUERY_CONTINUATION_SHORTCUT_EXACT = {
    "more",
    "next",
    "show more",
    "next page",
    "show transactions",
    "show my transactions",
    "list transactions",
    "show them",
    "which ones",
    "details",
    "show details",
    "receipt",
    "issue",
    "report issue",
    "last month",
    "this month",
    "yesterday",
    "today",
    "only debits",
    "only credits",
}
QUERY_CONTINUATION_SHORTCUT_BLOCKLIST_PATTERNS = (
    r"\bbalance\b",
    r"\baccounts?\b",
    r"\bairtime\b",
    r"\bdata\b",
    r"\bbeneficiar(?:y|ies)\b",
    r"\bsupport\b",
    r"\bhelp\b",
    r"\bsend\b",
    r"\btransfer\b",
)


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


def _build_fastpath_fallback_task(subtype: str, message_text: str) -> PlannedTask | None:
    """Build a read-only worker task when fastpath should not answer directly."""
    if subtype in CONTEXT_FASTPATH_ACCOUNT_SUBTYPES:
        return PlannedTask(
            task_id="t1",
            action="list_accounts",
            executor="account",
            instruction=message_text,
            parameters=TaskParameters(),
            risk="READ_ONLY",
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


def _detect_unsupported_capabilities(message_text: str) -> list[str]:
    """Resolve unsupported capabilities from policy-defined phrase patterns."""
    policy = get_cached_policy()
    text = message_text.lower().strip()

    if not text:
        return []

    configured_unsupported = policy.unsupported_capabilities
    pattern_map = policy.unsupported_detection

    detected_set: set[str] = set()
    for capability, patterns in pattern_map.items():
        if not patterns:
            continue
        normalized_patterns = [p.lower().strip() for p in patterns if p and p.strip()]
        if any(pattern in text for pattern in normalized_patterns):
            detected_set.add(capability)

    # Deterministic order for stable output/tests.
    ordered_detected = [cap for cap in configured_unsupported if cap in detected_set]
    return ordered_detected


def _resolve_unsupported_alternatives(unsupported: list[str]) -> list[str]:
    """Resolve up to two unique alternatives from policy."""
    policy = get_cached_policy()
    alternatives: list[str] = []

    for capability in unsupported:
        cap_alts = policy.unsupported_alternatives.get(capability, [])
        for alt in cap_alts:
            if alt and alt not in alternatives:
                alternatives.append(alt)
            if len(alternatives) >= 2:
                return alternatives
    return alternatives


def _build_locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    """Prepare loaded_context patch with updated locale."""
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _detected_locale_value(planner_output: Any) -> str | None:
    """Resolve detected locale value from planner output when present."""
    detected_language = getattr(planner_output, "detected_language", None)
    if not isinstance(detected_language, str) or not detected_language:
        return None
    return cast(str, LocaleManager.from_detection(detected_language).value)


def _build_policy_notice(message_text: str, planner_output: Any, locale: str = "en") -> str | None:
    if not planner_output or not planner_output.tasks:
        return None

    unsupported = _detect_unsupported_capabilities(message_text)
    if not unsupported:
        return None
    logger.info("unsupported_detected", capabilities=unsupported)

    supported_labels = []
    for executor in {t.executor for t in planner_output.tasks if t.executor in SUPPORTED_EXECUTOR_LABELS}:
        supported_labels.append(SUPPORTED_EXECUTOR_LABELS[executor])

    if not supported_labels:
        return None

    supported_text = ", ".join(sorted(supported_labels))
    unsupported_text = ", ".join(unsupported)
    alternatives = _resolve_unsupported_alternatives(unsupported)
    return cast(
        str,
        render_policy_notice(
            locale=locale,
            supported_text=supported_text,
            unsupported_text=unsupported_text,
            alternatives=alternatives,
        ),
    )


def _filter_spurious_affirmation_tasks(
    planner_output: Any,
    *,
    active_intent: str | None,
    pending_interrupt_kind: str | None,
) -> Any:
    """Drop accidental support tasks when a bare resume-style affirmation is detected."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output
    if not bool(getattr(planner_output, "is_confirmation", False)):
        return planner_output

    tasks = list(planner_output.tasks)
    has_resume = any(
        getattr(task, "executor", None) == "orchestrator" and getattr(task, "action", None) == "resume_session"
        for task in tasks
    )
    has_transaction_task = any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in tasks)
    in_transaction_input_flow = active_intent in TRANSACTION_EXECUTORS and pending_interrupt_kind == "input"
    should_strip_support = has_resume or has_transaction_task or in_transaction_input_flow
    if not should_strip_support:
        return planner_output

    filtered_tasks = [task for task in tasks if getattr(task, "executor", None) != "support"]
    if len(filtered_tasks) == len(tasks):
        return planner_output

    planner_output.tasks = filtered_tasks
    if len(filtered_tasks) == 1:
        planner_output.primary_intent = getattr(filtered_tasks[0], "executor", planner_output.primary_intent)
        planner_output.is_complex = False
    logger.info(
        "spurious_support_task_removed",
        original_count=len(tasks),
        filtered_count=len(filtered_tasks),
        active_intent=active_intent,
        pending_interrupt_kind=pending_interrupt_kind,
    )
    return planner_output


def _has_pending_mandate_without_ready_accounts(loaded_context: dict[str, Any] | None) -> bool:
    if not isinstance(loaded_context, dict):
        return False
    accounts_raw = loaded_context.get("accounts")
    if not isinstance(accounts_raw, list):
        return False

    has_ready = False
    has_pending_like = False
    for account in accounts_raw:
        if not isinstance(account, dict):
            continue
        status = str(account.get("mandate_status") or "").strip().lower()
        if not status:
            continue
        if status == "ready":
            has_ready = True
        else:
            has_pending_like = True

    return has_pending_like and not has_ready


def _deescalate_mandate_acknowledgement(
    planner_output: Any,
    *,
    loaded_context: dict[str, Any] | None,
    locale: str,
) -> Any:
    """Keep pending-mandate turns conversational with contextual destination details."""
    if not planner_output or not getattr(planner_output, "tasks", None):
        return planner_output
    if not _has_pending_mandate_without_ready_accounts(loaded_context):
        return planner_output

    tasks = list(planner_output.tasks)
    has_transaction_task = any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in tasks)
    if not has_transaction_task:
        return planner_output

    planner_output.tasks = []
    planner_output.primary_intent = "conversational"
    planner_output.is_complex = False
    accounts = loaded_context.get("accounts") if isinstance(loaded_context, dict) else []
    normalized_accounts = (
        [account for account in accounts if isinstance(account, dict)] if isinstance(accounts, list) else []
    )
    planner_output.response = build_pending_mandate_message(normalized_accounts, locale)
    planner_output.response_key = None

    logger.info(
        "pending_mandate_acknowledgement_deescalated",
        original_task_count=len(tasks),
    )
    return planner_output


def _build_quoted_replay_context(state: OrchestratorState) -> str:
    return _clip_text(
        (
            f"Quoted message id: {state.quoted_message_id or 'unknown'}\n"
            f"Has quote: {state.has_quote}\n"
            "Quoted actionable payload: unavailable"
        ),
        1800,
    )


def _build_quoted_replay_context_with_payload(state: OrchestratorState, quoted_payload: dict[str, Any]) -> str:
    user_state = _build_user_state_summary(state) or "User State: unavailable"
    payload_preview = _compact_payload_for_prompt(quoted_payload)
    return _clip_text(
        (
            f"Quoted message id: {state.quoted_message_id or 'unknown'}\n"
            f"Has quote: {state.has_quote}\n"
            f"Quoted actionable payload: {payload_preview}\n"
            f"{user_state}"
        ),
        1800,
    )


def _next_quoted_replay_task_id(state: OrchestratorState, task_type: str, existing_ids: set[str] | None = None) -> str:
    seen = set(existing_ids or set())
    seen.update(state.tasks.keys())
    idx = 1
    task_id = f"quoted_replay_{task_type}_{idx}"
    while task_id in seen:
        idx += 1
        task_id = f"quoted_replay_{task_type}_{idx}"
    return task_id


async def _load_quoted_actionable_payload(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any] | None:
    repo = config["configurable"].get("actionable_message_repo")
    if repo is None or not state.quoted_message_id:
        return None

    user_id = (state.loaded_context or {}).get("user_id") or state.user_id
    if not user_id:
        logger.info("quoted_replay_actionable_lookup_skipped", reason="missing_user_id")
        return None

    try:
        row = await repo.get_by_channel_message_id_for_user(state.quoted_message_id, str(user_id))
    except Exception as exc:
        logger.warning("quoted_replay_actionable_lookup_failed", error=str(exc))
        return None

    if not row:
        return None

    message_data = row.get("message_data") if isinstance(row, dict) else getattr(row, "message_data", None)
    if isinstance(message_data, dict):
        return dict(message_data)
    return None


def _is_replay_payload_sufficient(task_type: str, payload: dict[str, Any]) -> bool:
    if task_type == "airtime":
        return bool(
            payload.get("amount") is not None and (payload.get("recipient_phone") or payload.get("target_phone"))
        )
    if task_type == "data":
        return bool(
            (payload.get("target_phone") or payload.get("recipient_phone"))
            and (payload.get("amount") is not None or payload.get("plan_code") or payload.get("plan_name"))
        )
    return bool(
        payload.get("amount") is not None
        and (
            payload.get("beneficiary_id")
            or payload.get("recipient_account")
            or payload.get("recipient_name")
            or payload.get("recipient_phone")
        )
    )


def _sanitize_replay_task_payload(*, task_type: str, payload: dict[str, Any], text: str) -> dict[str, Any] | None:
    next_payload = dict(payload)
    if "action" not in next_payload:
        next_payload["action"] = {"transfer": "send_money", "airtime": "buy_airtime", "data": "buy_data"}[task_type]
    next_payload.setdefault("instruction", text)
    next_payload.setdefault("message", text)
    next_payload["skip_extraction"] = True
    confirmation = next_payload.get("confirmation")
    if not isinstance(confirmation, dict):
        confirmation = {}
    confirmation["confirmed"] = False
    next_payload["confirmation"] = confirmation
    next_payload["idempotency_key"] = None
    next_payload["transaction_id"] = None

    if not _is_replay_payload_sufficient(task_type, next_payload):
        return None
    return next_payload


def _build_quoted_replay_execution_updates(
    *,
    state: OrchestratorState,
    text: str,
    interpretation: QuotedReplayInterpretation,
    locale_updates: dict[str, Any],
) -> dict[str, Any] | None:
    new_tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    for item in interpretation.tasks:
        task_type = item.task_type
        payload = item.payload.model_dump(exclude_none=True)
        sanitized_payload = _sanitize_replay_task_payload(task_type=task_type, payload=payload, text=text)
        if sanitized_payload is None:
            continue
        task_id = _next_quoted_replay_task_id(state, task_type, allocated_ids)
        allocated_ids.add(task_id)
        new_tasks[task_id] = TaskSpec(
            id=task_id,
            type=cast(Any, task_type),
            stage=TaskStage.DRAFT,
            payload=sanitized_payload,
        )
        wave_ids.append(task_id)

    if not wave_ids:
        return None

    logger.info(
        "quoted_replay_shortcut_hit",
        decision=interpretation.decision,
        task_count=len(wave_ids),
    )
    return {
        "tasks": new_tasks,
        "waves": [wave_ids],
        "current_wave_index": 0,
        "normalized_instruction": text,
        **locale_updates,
    }


def _quoted_replay_clarify_response(interpretation: QuotedReplayInterpretation, locale: str) -> str:
    return interpretation.clarify_message or render_message("conversational.clarify", locale)


def _build_policy_aware_greeting(locale: str) -> str:
    policy = get_cached_policy()
    supported = ", ".join(policy.supported_domains)
    return cast(
        str,
        render_message(
            "meta.fallback",
            locale,
            {
                "name": policy.identity.name,
                "description": policy.identity.description,
                "supported": supported,
            },
        ),
    )


def _meta_intent_from_response_key(response_key: str | None) -> MetaIntent | None:
    if not response_key:
        return None
    return META_RESPONSE_KEY_TO_INTENT.get(response_key)


def _normalize_shortcut_message(message: str) -> str:
    return " ".join(message.lower().strip().split())


def _looks_like_explicit_query_continuation(message_text: str) -> bool:
    normalized = _normalize_shortcut_message(message_text)
    if not normalized:
        return False
    if normalized in QUERY_CONTINUATION_SHORTCUT_EXACT:
        return True
    if re.fullmatch(r"(only|just)\s+(credits?|debits?)", normalized):
        return True
    if re.fullmatch(r"(last|recent)\s+\d+", normalized):
        return True
    return False


def _is_query_continuation_blocked(message_text: str) -> bool:
    normalized = _normalize_shortcut_message(message_text)
    return any(re.search(pattern, normalized) for pattern in QUERY_CONTINUATION_SHORTCUT_BLOCKLIST_PATTERNS)


def _next_query_continuation_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = f"query_continuation_{idx}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"query_continuation_{idx}"
    return task_id


def _next_transfer_fanout_task_id(base_task_id: str, index: int, existing_ids: set[str]) -> str:
    candidate = f"{base_task_id}_r{index}"
    while candidate in existing_ids:
        index += 1
        candidate = f"{base_task_id}_r{index}"
    existing_ids.add(candidate)
    return candidate


def _expand_underproduced_transfer_tasks(
    planned_tasks: list[PlannedTask],
    user_text: str,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Fan out a single transfer task when user text clearly contains multiple recipients."""
    transfer_indices = [idx for idx, task in enumerate(planned_tasks) if task.executor == "transfer"]
    if len(transfer_indices) != 1:
        return planned_tasks, None

    recipients = _derive_recipients_from_user_text(user_text)
    if len(recipients) < 2:
        return planned_tasks, None

    source_index = transfer_indices[0]
    source_task = planned_tasks[source_index]
    source_parameters = source_task.parameters

    # Keep parser repair narrow: avoid fanout when task is account+bank explicit or purely reference-based.
    if source_parameters.recipient_account or source_parameters.bank_name:
        return planned_tasks, None
    if source_parameters.reference and not (source_parameters.recipient or source_parameters.recipient_name):
        return planned_tasks, None

    existing_ids = {task.task_id for task in planned_tasks}
    expanded_task_ids: list[str] = [source_task.task_id]
    expanded_source_tasks: list[PlannedTask] = []

    first_task = source_task.model_copy(deep=True)
    first_task.parameters.recipient = recipients[0]
    first_task.parameters.recipient_name = recipients[0]
    expanded_source_tasks.append(first_task)

    for idx, recipient in enumerate(recipients[1:], start=2):
        clone = source_task.model_copy(deep=True)
        clone.task_id = _next_transfer_fanout_task_id(source_task.task_id, idx, existing_ids)
        clone.parameters.recipient = recipient
        clone.parameters.recipient_name = recipient
        expanded_source_tasks.append(clone)
        expanded_task_ids.append(clone.task_id)

    expanded_tasks: list[PlannedTask] = []
    for idx, task in enumerate(planned_tasks):
        if idx == source_index:
            expanded_tasks.extend(expanded_source_tasks)
            continue

        copy_task = task.model_copy(deep=True)
        if source_task.task_id in copy_task.depends_on:
            rewritten: list[str] = []
            for dep in copy_task.depends_on:
                if dep == source_task.task_id:
                    rewritten.extend(expanded_task_ids)
                else:
                    rewritten.append(dep)
            copy_task.depends_on = list(dict.fromkeys(rewritten))
        expanded_tasks.append(copy_task)

    return (
        expanded_tasks,
        {
            "source_task_id": source_task.task_id,
            "recipient_count": len(expanded_task_ids),
            "recipient_names": recipients,
        },
    )


def _should_replan_active_wave(state: OrchestratorState) -> bool:
    """Allow replanning active waves only for explicit pre-execution update turns."""
    if not state.waves or state.pending_interrupt is not None:
        return False
    if not (state.last_message_text or "").strip():
        return False
    if state.current_wave_index >= len(state.waves):
        return False

    current_wave = state.waves[state.current_wave_index]
    if not current_wave:
        return False

    replannable_stages = {TaskStage.AWAITING_CONFIRMATION, TaskStage.AWAITING_AUTH}
    active_tasks = [state.tasks.get(task_id) for task_id in current_wave if state.tasks.get(task_id) is not None]
    if not active_tasks:
        return False
    return all(task.stage in replannable_stages for task in active_tasks)


def _strip_transactional_depends_on_edges(
    planned_tasks: list[PlannedTask],
) -> tuple[list[PlannedTask], list[tuple[str, str]]]:
    """Remove depends_on edges between transaction tasks for single-batch auth collection."""
    executor_by_task_id = {task.task_id: task.executor for task in planned_tasks}
    stripped_edges: list[tuple[str, str]] = []
    normalized_tasks: list[PlannedTask] = []

    for task in planned_tasks:
        copy_task = task.model_copy(deep=True)
        if copy_task.executor not in TRANSACTION_EXECUTORS:
            normalized_tasks.append(copy_task)
            continue

        next_depends_on: list[str] = []
        seen: set[str] = set()
        for dep_task_id in copy_task.depends_on:
            dep_executor = executor_by_task_id.get(dep_task_id)
            if dep_executor in TRANSACTION_EXECUTORS:
                stripped_edges.append((dep_task_id, copy_task.task_id))
                continue
            if dep_task_id in seen:
                continue
            seen.add(dep_task_id)
            next_depends_on.append(dep_task_id)
        copy_task.depends_on = next_depends_on
        normalized_tasks.append(copy_task)

    return normalized_tasks, stripped_edges


async def plan_tasks(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Planner Node.

    1. If new request (no active waves), call Planner to create TaskSpecs.
    2. If existing waves, this is a pass-through (or bulk extraction update).
    """
    if state.waves and state.pending_interrupt is None and not _should_replan_active_wave(state):
        return {}

    task_planner = config["configurable"].get("task_planner")
    text = state.last_message_text or ""
    current_locale = LocaleManager.normalize(state.loaded_context.get("language")).value
    redis_client = config["configurable"].get("redis_client")
    if task_planner is None:
        logger.error("task_planner_missing")
        return {"final_response": render_safe_capability_fallback(current_locale)}

    locale_updates = _build_locale_update(state, current_locale)

    if state.has_quote and state.quoted_message_id and hasattr(task_planner, "interpret_quoted_replay"):
        quoted_payload = await _load_quoted_actionable_payload(state, config)
        quoted_context = (
            _build_quoted_replay_context_with_payload(state, quoted_payload)
            if quoted_payload is not None
            else _build_quoted_replay_context(state)
        )
        try:
            interpretation = cast(
                QuotedReplayInterpretation,
                await task_planner.interpret_quoted_replay(state.phone_number, text, context=quoted_context),
            )
            if interpretation.decision == "clarify":
                logger.info("quoted_replay_shortcut_clarify", reason=interpretation.reason)
                return {
                    "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            if interpretation.decision == "execute":
                if interpretation.confidence < QUOTED_REPLAY_MIN_CONFIDENCE:
                    logger.info(
                        "quoted_replay_confidence_low",
                        decision=interpretation.decision,
                        confidence=interpretation.confidence,
                        min_confidence=QUOTED_REPLAY_MIN_CONFIDENCE,
                    )
                    return {
                        "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                        "normalized_instruction": text,
                        **locale_updates,
                    }
                if quoted_payload is None:
                    logger.info("quoted_replay_actionable_payload_missing", quoted_message_id=state.quoted_message_id)
                    return {
                        "final_response": render_message("conversational.clarify", current_locale),
                        "normalized_instruction": text,
                        **locale_updates,
                    }
                replay_updates = _build_quoted_replay_execution_updates(
                    state=state,
                    text=text,
                    interpretation=interpretation,
                    locale_updates=locale_updates,
                )
                if replay_updates is not None:
                    return replay_updates
                logger.info(
                    "quoted_replay_insufficient_payload",
                    decision=interpretation.decision,
                    reason=interpretation.reason,
                )
                return {
                    "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            logger.info("quoted_replay_shortcut_miss", decision=interpretation.decision, reason=interpretation.reason)
        except Exception as exc:
            logger.warning("quoted_replay_shortcut_failed", error=str(exc))

    planner_context_sections: list[tuple[str, str]] = []
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None
    current_flow_type: str | None = None
    if state.waves and state.current_wave_index < len(state.waves):
        current_wave = state.waves[state.current_wave_index]
        if current_wave:
            wave_task = state.tasks.get(current_wave[0])
            if wave_task:
                current_flow_type = wave_task.type
    is_transactional_flow = current_flow_type in TRANSACTION_EXECUTORS

    if redis_client:
        try:
            import asyncio

            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            query_session_key = f"query:session:{state.phone_number}"
            suggestion_data, query_session_data = await asyncio.gather(
                redis_client.get(suggestion_key),
                redis_client.get(query_session_key),
            )

            if suggestion_data:
                import json

                data = json.loads(suggestion_data)
                name = data.get("recipient_name") or data.get("alias_suggested") or "Unknown"
                planner_context_sections.append(
                    (
                        "beneficiary_suggestion",
                        _clip_text(
                            (
                                f"Active Context: User was asked to save beneficiary '{name}'.\n"
                                f"- Reply 'yes'/'save' -> Save with name '{name}'.\n"
                                "- Reply with explicit alias intent (e.g., 'save as Mum')"
                                " -> Save with that alias.\n"
                                "- Greetings/check-ins/thanks are NOT save intent."
                            ),
                            PLANNER_CONTEXT_BENEFICIARY_SUGGESTION_MAX_CHARS,
                        ),
                    )
                )
                logger.info("planner_context_injected", context="beneficiary_suggestion")

            if query_session_data:
                import json

                session = json.loads(query_session_data)
                if isinstance(session, dict):
                    query_session_snapshot = session
                    query_session_source = "redis"

            if query_session_snapshot and not is_transactional_flow:
                session = query_session_snapshot
                session_active = bool(session.get("session_active"))
                if (
                    session_active
                    and state.pending_interrupt is None
                    and _looks_like_explicit_query_continuation(text)
                    and not _is_query_continuation_blocked(text)
                ):
                    shortcut_task_id = _next_query_continuation_task_id(state.tasks)
                    shortcut_task = TaskSpec(
                        id=shortcut_task_id,
                        type="query",
                        stage=TaskStage.DRAFT,
                        payload={
                            "action": "transaction_list",
                            "instruction": text,
                            "message": text,
                        },
                    )
                    logger.info("planner_query_continuation_shortcut_hit", message=text)
                    return {
                        "tasks": {shortcut_task_id: shortcut_task},
                        "waves": [[shortcut_task_id]],
                        "current_wave_index": 0,
                        "normalized_instruction": text,
                        **locale_updates,
                    }

                summary_text = None
                query_result = session.get("query_result")
                if isinstance(query_result, dict):
                    summary_text = query_result.get("summary_text")
                planner_context_sections.append(
                    (
                        "query_session",
                        _clip_text(
                            _build_query_session_context(
                                cast(str | None, summary_text) if isinstance(summary_text, str) else None
                            ),
                            PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS,
                        ),
                    )
                )
                logger.info("planner_context_injected", context="query_session")
            elif query_session_snapshot and is_transactional_flow:
                logger.info("planner_query_context_skipped", reason="active_transaction_flow")
        except Exception as e:
            logger.warning("planner_context_check_failed", error=str(e))

    if query_session_snapshot is None and isinstance(state.stashed_query_session, dict):
        query_session_snapshot = dict(state.stashed_query_session)
        query_session_source = "stashed"

    if query_session_snapshot and query_session_source == "stashed" and not is_transactional_flow:
        summary_text = None
        query_result = query_session_snapshot.get("query_result")
        if isinstance(query_result, dict):
            summary_text = query_result.get("summary_text")
        planner_context_sections.append(
            (
                "query_session_stashed",
                _clip_text(
                    _build_query_session_context(
                        cast(str | None, summary_text) if isinstance(summary_text, str) else None
                    ),
                    PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="query_session_stashed")
    elif query_session_snapshot and query_session_source == "stashed" and is_transactional_flow:
        logger.info("planner_query_context_skipped", reason="active_transaction_flow_stashed")

    active_intent = None
    if state.waves:
        try:
            current_wave = state.waves[state.current_wave_index]
            if current_wave:
                t_id = current_wave[0]
                if t_id in state.tasks:
                    active_task = state.tasks[t_id]
                    active_intent = active_task.type

                    payload_view = {
                        k: v for k, v in active_task.payload.items() if k not in ["result", "error", "confirmation"]
                    }
                    payload_preview = _compact_payload_for_prompt(payload_view)

                    planner_context_sections.append(
                        (
                            "active_flow",
                            _clip_text(
                                (
                                    f"Active Flow: {active_intent.upper()} (User is currently in this flow).\n"
                                    f"Current Task Data: {payload_preview}\n"
                                    "Review Rule 9 (CONTEXT OVERRIDE):"
                                    f"- Slot-filling/updates keep intent='{active_intent}'.\n"
                                    "- Clearly unrelated asks switch intent."
                                ),
                                PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS,
                            ),
                        )
                    )
                    logger.info("planner_context_active_flow_injected", intent=active_intent)
        except Exception as e:
            logger.warning("active_flow_context_failed", error=str(e))

    ctx_manager = OrchestratorContextManager()
    short_term_context = ctx_manager.build_llm_summary(state)

    if short_term_context:
        planner_context_sections.append(
            ("short_term_memory", _clip_text(short_term_context, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="short_term_memory")

    recent_domain_focus = _infer_recent_domain_focus(state)
    if recent_domain_focus:
        planner_context_sections.append(
            (
                "recent_domain_focus",
                _clip_text(
                    (
                        f"Recent Domain Focus: {recent_domain_focus}\n"
                        "- Referential/underspecified follow-ups should keep this domain.\n"
                        "- Switch only when user clearly asks another domain."
                    ),
                    PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="recent_domain_focus", domain=recent_domain_focus)

    user_state_summary = _build_user_state_summary(state)
    if user_state_summary:
        planner_context_sections.append(
            ("user_state_history", _clip_text(user_state_summary, PLANNER_CONTEXT_USER_STATE_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="user_state_history")

    planner_context, included_sections, clipped_sections, dropped_sections = _assemble_planner_context(
        planner_context_sections,
        max_chars=PLANNER_CONTEXT_MAX_CHARS,
    )
    logger.info(
        "planner_context_size",
        chars=len(planner_context),
        truncated=bool(clipped_sections),
        sections=len(included_sections),
        clipped_sections=clipped_sections,
        dropped_sections=dropped_sections,
    )

    fastpath_context_updates: dict[str, Any] = {}

    try:
        planner_output = await task_planner.plan_tasks(state.phone_number, text, context=planner_context)
        planner_output = _filter_spurious_affirmation_tasks(
            planner_output,
            active_intent=active_intent,
            pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
        )
        planner_output = _deescalate_mandate_acknowledgement(
            planner_output,
            loaded_context=state.loaded_context,
            locale=current_locale,
        )
        logger.info("planner_tasks_generated", output=planner_output)

        fastpath_subtype = _planner_fastpath_subtype(planner_output)
        if fastpath_subtype:
            has_context_for_fastpath = _has_context_for_fastpath_subtype(state, fastpath_subtype)
            has_no_tasks = not planner_output.tasks
            is_conversational_no_task = planner_output.primary_intent == "conversational" and has_no_tasks
            is_flow_fastpath = fastpath_subtype in CONTEXT_FASTPATH_FLOW_SUBTYPES

            if is_conversational_no_task and has_context_for_fastpath:
                logger.info("context_fastpath_hit", subtype=fastpath_subtype)
                total_items = _context_fastpath_total_items(state, fastpath_subtype)
                shown_limit = _context_fastpath_shown_limit(fastpath_subtype)
                if total_items is not None and total_items > shown_limit:
                    logger.info(
                        "context_fastpath_list_truncated",
                        subtype=fastpath_subtype,
                        shown=shown_limit,
                        total=total_items,
                    )
            else:
                if not has_context_for_fastpath:
                    fallback_reason = "insufficient_context"
                elif has_no_tasks:
                    fallback_reason = "invalid_no_task_shape"
                else:
                    fallback_reason = "planner_emitted_task"

                if has_no_tasks:
                    fallback_task = _build_fastpath_fallback_task(fastpath_subtype, text)
                    if fallback_task:
                        planner_output.tasks = [fallback_task]
                        planner_output.primary_intent = fallback_task.executor
                        planner_output.is_complex = False
                        planner_output.response = ""
                        planner_output.response_key = None
                        logger.info(
                            "context_fastpath_fallback_to_worker",
                            subtype=fastpath_subtype,
                            reason=fallback_reason,
                        )
                    elif is_flow_fastpath and not has_context_for_fastpath:
                        planner_output.primary_intent = "conversational"
                        planner_output.tasks = []
                        planner_output.is_complex = False
                        planner_output.response_key = None
                        if not planner_output.response:
                            planner_output.response = NO_ACTIVE_FLOW_FASTPATH_MESSAGE
                        logger.info("interrupt_status_query_no_active_flow", subtype=fastpath_subtype)
                elif is_flow_fastpath:
                    logger.info(
                        "context_fastpath_fallback_to_worker",
                        subtype=fastpath_subtype,
                        reason=fallback_reason,
                    )
                else:
                    logger.info(
                        "context_fastpath_fallback_to_worker",
                        subtype=fastpath_subtype,
                        reason=fallback_reason,
                    )

        detected_language = getattr(planner_output, "detected_language", None)
        if detected_language:
            if redis_client:
                signal = LanguageDetectionSignal(
                    locale=LocaleManager.from_detection(detected_language),
                    confidence=float(getattr(planner_output, "confidence", 1.0) or 0.0),
                    source="planner",
                    explicit=False,
                )
                resolved_locale = await LocaleManager.update_locale(state.phone_number, signal)
                current_locale = resolved_locale.value
            else:
                current_locale = LocaleManager.from_detection(detected_language).value

        if redis_client and planner_context != "None" and planner_output and planner_output.tasks:
            is_saving = any(
                t.executor == "beneficiary" and t.action == "save_beneficiary" for t in planner_output.tasks
            )
            if not is_saving:
                suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
                await redis_client.delete(suggestion_key)
                logger.info("cleared_stale_beneficiary_context", phone=state.phone_number)

        fastpath_context_updates = _build_beneficiary_fastpath_context_updates(
            state,
            planner_output,
            fastpath_subtype,
        )

    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return {}

    detected_locale = _detected_locale_value(planner_output)

    def _localized_planner_response(raw_response: str | None) -> str:
        if not raw_response:
            return ""
        return cast(str, render_text(raw_response, current_locale))

    if getattr(planner_output, "is_cancellation", False) or planner_output.primary_intent == "cancel":
        logger.info("planner_cancellation_detected", intent=planner_output.primary_intent)
        cancel_locale = detected_locale or current_locale
        cancel_locale_updates = (
            locale_updates if cancel_locale == current_locale else _build_locale_update(state, cancel_locale)
        )
        if planner_output.response_key == "planner.cancelled":
            logger.info("planner_response_key_used", key=planner_output.response_key, locale=cancel_locale)
            cancel_message = render_message(planner_output.response_key, cancel_locale)
        else:
            cancel_message = _localized_planner_response(planner_output.response) or render_message(
                "planner.cancelled", cancel_locale
            )
        return {
            "waves": [],
            "final_response": cancel_message,
            **cancel_locale_updates,
        }

    if not planner_output or not planner_output.tasks:
        if planner_output and planner_output.primary_intent == "conversational":
            conversational_locale = detected_locale or current_locale
            conversational_locale_updates = (
                locale_updates
                if conversational_locale == current_locale
                else _build_locale_update(state, conversational_locale)
            )
            if planner_output.response:
                logger.info("planner_direct_response_used", locale=conversational_locale)
                return {
                    "final_response": _localized_planner_response(planner_output.response),
                    **conversational_locale_updates,
                    **fastpath_context_updates,
                }

            response_key = planner_output.response_key
            if response_key:
                logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
                if response_key == "conversational.greeting":
                    return {
                        "final_response": _build_policy_aware_greeting(conversational_locale),
                        **conversational_locale_updates,
                        **fastpath_context_updates,
                    }

                meta_intent = _meta_intent_from_response_key(response_key)
                llm = getattr(task_planner, "planner_llm", None)
                if meta_intent and llm is not None and hasattr(llm, "with_structured_output"):
                    meta_message, handoff = await generate_meta_reply(
                        llm,
                        user_message=text,
                        user_language_hint=conversational_locale,
                        meta_intent=meta_intent,
                        redis_client=redis_client,
                    )
                    if handoff == "meta" and meta_message:
                        logger.info(
                            "meta_query_route_hit",
                            source="response_key",
                            meta_kind=meta_intent.value,
                        )
                        logger.info(
                            "planner_meta_reply_used",
                            response_key=response_key,
                            locale=conversational_locale,
                            intent=meta_intent.value,
                        )
                        return {
                            "final_response": meta_message,
                            **conversational_locale_updates,
                            **fastpath_context_updates,
                        }
                    logger.info(
                        "planner_meta_reply_fallback",
                        response_key=response_key,
                        locale=conversational_locale,
                        handoff=handoff,
                    )
                return {
                    "final_response": render_message(response_key, conversational_locale),
                    **conversational_locale_updates,
                    **fastpath_context_updates,
                }

            logger.info(
                "planner_response_key_missing_and_no_response",
                intent=planner_output.primary_intent,
                detected_language=getattr(planner_output, "detected_language", None),
            )
            fallback_key: MessageKey = "conversational.clarify"
            logger.info("conversational_fallback_deterministic_used", key=fallback_key, locale=conversational_locale)
            return {
                "final_response": render_message(fallback_key, conversational_locale),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        if state.waves and planner_output and planner_output.primary_intent != "conversational":
            if planner_output.primary_intent != active_intent:
                logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
                return {
                    "waves": [],
                    "final_response": _localized_planner_response(planner_output.response),
                    **locale_updates,
                    **fastpath_context_updates,
                }

        if planner_output and planner_output.response:
            return {
                "final_response": _localized_planner_response(planner_output.response),
                **locale_updates,
                **fastpath_context_updates,
            }
        return {
            "final_response": render_safe_capability_fallback(current_locale),
            **locale_updates,
            **fastpath_context_updates,
        }

    if state.waves and active_intent:
        logger.info("planner_intent_switch_or_update", old=active_intent, new=planner_output.primary_intent)

    fanout_tasks, fanout_meta = _expand_underproduced_transfer_tasks(planner_output.tasks, text)
    if fanout_meta:
        planner_output.tasks = fanout_tasks
        planner_output.is_complex = True
        logger.info(
            "planner_transfer_multi_recipient_fanout_applied",
            source_task_id=fanout_meta["source_task_id"],
            recipient_count=fanout_meta["recipient_count"],
            recipient_names=fanout_meta["recipient_names"],
        )

    normalized_tasks, stripped_edges = _strip_transactional_depends_on_edges(planner_output.tasks)
    if stripped_edges:
        planner_output.tasks = normalized_tasks
        logger.info(
            "txn_dep_removed_for_batch_auth",
            source_task_ids=sorted({source for source, _ in stripped_edges}),
            target_task_ids=sorted({target for _, target in stripped_edges}),
            removed_edges=[f"{source}->{target}" for source, target in stripped_edges],
            removed_count=len(stripped_edges),
        )

    expected_executors = {
        str(item)
        for item in (state.preplanner_expected_transaction_executors or [])
        if str(item) in TRANSACTION_EXECUTORS
    }
    if expected_executors:
        planned_executors = {task.executor for task in planner_output.tasks if task.executor in TRANSACTION_EXECUTORS}
        missing_executors = sorted(expected_executors - planned_executors)
        if missing_executors:
            retry_context = _clip_text(
                (
                    f"{planner_context}\n\nPre-planner expected explicit transaction executors: "
                    f"{', '.join(sorted(expected_executors))}. "
                    f"Ensure all explicit executors are represented in tasks."
                ),
                PLANNER_CONTEXT_MAX_CHARS,
            )
            try:
                retry_output = await task_planner.plan_tasks(state.phone_number, text, context=retry_context)
                retry_output = _filter_spurious_affirmation_tasks(
                    retry_output,
                    active_intent=active_intent,
                    pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
                )
                retry_output = _deescalate_mandate_acknowledgement(
                    retry_output,
                    loaded_context=state.loaded_context,
                    locale=current_locale,
                )
                retry_executors = {
                    task.executor for task in retry_output.tasks if task.executor in TRANSACTION_EXECUTORS
                }
                if expected_executors.issubset(retry_executors):
                    planner_output = retry_output
                    logger.info(
                        "planner_expected_executor_retry_applied",
                        expected_executors=sorted(expected_executors),
                        missing_executors=missing_executors,
                    )
                else:
                    logger.warning(
                        "planner_expected_executor_retry_rejected",
                        expected_executors=sorted(expected_executors),
                        retry_executors=sorted(retry_executors),
                    )
            except Exception as exc:
                logger.warning(
                    "planner_expected_executor_retry_failed",
                    error=str(exc),
                    expected_executors=sorted(expected_executors),
                    missing_executors=missing_executors,
                )
    stashed_query_session_update: dict[str, Any] | None = None
    if (
        query_session_source == "redis"
        and query_session_snapshot
        and bool(query_session_snapshot.get("session_active"))
        and any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in planner_output.tasks)
    ):
        stash_keys = (
            "session_active",
            "query_contract",
            "query_result",
            "surface",
            "show_expanded",
            "current_page",
            "page_size",
            "account_id",
            "account_ids",
            "cached_transactions",
            "cache_fetched_at",
            "cache_fingerprint",
            "timestamp",
        )
        stashed_query_session_update = {
            key: query_session_snapshot.get(key) for key in stash_keys if key in query_session_snapshot
        }
        stashed_query_session_update["session_active"] = True
        logger.info(
            "planner_query_session_stashed_for_transaction_switch",
            keys=list(stashed_query_session_update.keys()),
        )

    new_tasks = {}
    task_ids: list[str] = []
    depends_on_by_task: dict[str, list[str]] = {}

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
        task_ids.append(spec.id)
        depends_on_by_task[spec.id] = list(spec.depends_on)

    waves = build_dependency_waves(task_ids, depends_on_by_task)

    policy_notice = _build_policy_notice(text, planner_output, current_locale)
    if policy_notice:
        logger.info("policy_notice_created")

    return {
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
        "policy_notice": policy_notice,
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state.stashed_query_session
        ),
        **locale_updates,
    }
