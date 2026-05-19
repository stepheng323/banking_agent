import re
from typing import Any

from apps.chat.src.agent.graphs.__shared__.account_selection.reference import match_source_account_reference
from apps.chat.src.agent.graphs.__shared__.beneficiary.selection import match_beneficiary_candidate_selection
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.confirmation_resolve import (
    _digits_only,
    _normalize_recipient_match_text,
    _select_confirmation_continue_flow_task_ids,
    _stash_previous_confirmation_snapshots,
    _synth_confirmation_followup_message,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import logger
from apps.chat.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from shared.types.planner import (
    InterruptRouteDecision,
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
_INPUT_SIMPLE_AMOUNT_REPLY_RE = re.compile(r"^(?:₦?\d[\d,]*(?:\.\d+)?k?|all|everything|half|50%)$", re.IGNORECASE)
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

def _resolve_deterministic_input_selection_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "input":
        return None

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = fields_by_task.get(task_ids[0]) or []
    if set(required_fields) != {"source_account_id"}:
        return None
    if not text.strip().isdigit():
        loaded_context = state.loaded_context if isinstance(state.loaded_context, dict) else {}
        accounts = (
            loaded_context.get("transaction_accounts")
            or loaded_context.get("accounts")
            or loaded_context.get("all_accounts")
        )
        if not isinstance(accounts, list) or not match_source_account_reference(
            text,
            [account for account in accounts if isinstance(account, dict)],
        ):
            return None

    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=0.99,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason="shortcut_input_numeric_selection",
    )

def _is_beneficiary_clarification_interrupt(interrupt: Any) -> bool:
    if not interrupt or getattr(interrupt, "kind", None) != "input":
        return False
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return False
    return any(isinstance(fields, list) and "beneficiary_id" in fields for fields in fields_by_task.values())

def _resolve_deterministic_input_slot_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    if getattr(interrupt, "kind", None) != "input":
        return None

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return None

    active_task = state.tasks.get(str(task_ids[0]))
    active_task_type = active_task.type if active_task is not None else None

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = {
        field
        for field in (fields_by_task.get(str(task_ids[0])) or [])
        if isinstance(field, str)
    }
    if not required_fields:
        return None

    stripped_text = text.strip()
    numeric_text = _digits_only(text)

    if required_fields == {"beneficiary_id"}:
        beneficiary_candidates = []
        if active_task is not None and isinstance(active_task.payload, dict):
            raw_candidates = active_task.payload.get("beneficiary_candidates")
            if isinstance(raw_candidates, list):
                beneficiary_candidates = [c for c in raw_candidates if isinstance(c, dict)]
        if stripped_text.isdigit() or match_beneficiary_candidate_selection(stripped_text, beneficiary_candidates):
            return InterruptRouteDecision(
                decision="continue_flow",
                confidence=0.99,
                detected_language="English",
                target_intent=None,
                target_mode=None,
                status_query_type=None,
                reason="shortcut_input_beneficiary_selection",
            )

    if required_fields == {"recipient_account"} and 8 <= len(numeric_text) <= 16:
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type=None,
            reason="shortcut_input_account_entry",
        )

    if required_fields in ({"recipient_phone"}, {"phone"}, {"target_phone"}) and 10 <= len(numeric_text) <= 15:
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type=None,
            reason="shortcut_input_phone_entry",
        )

    if required_fields == {"amount"} and _INPUT_SIMPLE_AMOUNT_REPLY_RE.fullmatch(stripped_text):
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.99,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type=None,
            reason="shortcut_input_amount_entry",
        )

    if (
        active_task_type == "transfer"
        and required_fields == {"recipient_account", "recipient_bank_name"}
        and _looks_like_simple_transfer_recipient_reply(stripped_text)
    ):
        return InterruptRouteDecision(
            decision="continue_flow",
            confidence=0.95,
            detected_language="English",
            target_intent=None,
            target_mode=None,
            status_query_type=None,
            reason="shortcut_input_recipient_reply",
        )

    return None

def _looks_like_simple_transfer_recipient_reply(text: str) -> bool:
    stripped_text = text.strip()
    if not stripped_text:
        return False
    if _INPUT_RECIPIENT_REPLY_META_RE.fullmatch(stripped_text):
        return False
    if "?" in stripped_text or _INPUT_RECIPIENT_REPLY_QUESTION_RE.search(stripped_text):
        return False
    if _NON_TRANSFER_INTENT_HINT_RE.search(stripped_text) or _INPUT_RECIPIENT_REPLY_BLOCK_RE.search(stripped_text):
        return False

    match = _INPUT_RECIPIENT_REPLY_PREFIX_RE.fullmatch(stripped_text)
    if match is None:
        return False

    candidate = (match.group("recipient") or "").strip(" .,!?:;\"'()[]{}")
    if not candidate:
        return False

    if _digits_only(candidate):
        return False

    normalized_candidate = _normalize_recipient_match_text(candidate)
    if not normalized_candidate:
        return False

    tokens = normalized_candidate.split()
    if not tokens or len(tokens) > 4:
        return False

    return all(len(token) >= 2 for token in tokens)

def _continue_flow_updates(
    state: OrchestratorState,
    interrupt: Any,
    precomputed_payload_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if interrupt.kind in {"input", "confirmation"}:
        task_ids_to_reset = [str(task_id) for task_id in interrupt.task_ids]
        selection_reason = "input_flow"
        matched_task_ids: list[str] = []
        payload_overrides: dict[str, dict[str, Any]] = dict(precomputed_payload_overrides or {})
        message_overrides: dict[str, str] = {}
        if interrupt.kind == "confirmation":
            task_ids_to_reset, selection_reason, matched_task_ids = _select_confirmation_continue_flow_task_ids(
                state,
                interrupt,
            )
            original_task_ids = [str(task_id) for task_id in interrupt.task_ids]
            if payload_overrides:
                task_ids_to_reset = [task_id for task_id in original_task_ids if task_id in payload_overrides]
                selection_reason = "precomputed_correction_scope"
        logger.info(
            "confirmation_update_detected",
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
        if interrupt.kind == "confirmation":
            if payload_overrides:
                logger.info(
                    "confirmation_task_payload_overrides_applied",
                    task_ids=sorted(payload_overrides.keys()),
                )
            if message_overrides:
                logger.info(
                    "confirmation_task_message_overrides_applied",
                    task_ids=sorted(message_overrides.keys()),
                )
            for task_id in task_ids_to_reset:
                task = state.tasks.get(task_id)
                if task is None:
                    continue
                if task_id in payload_overrides:
                    task.payload.update(payload_overrides[task_id])
                if task_id in message_overrides:
                    task.payload["pending_user_message"] = message_overrides[task_id]
                    task.payload["confirmation_message_scoped"] = True
                elif task_id in payload_overrides:
                    synthesized = _synth_confirmation_followup_message(task)
                    if synthesized:
                        task.payload["pending_user_message"] = synthesized
                        task.payload["confirmation_message_scoped"] = True
                    else:
                        task.payload.pop("pending_user_message", None)
                        task.payload.pop("confirmation_message_scoped", None)
                else:
                    task.payload.pop("pending_user_message", None)
                    task.payload.pop("confirmation_message_scoped", None)
        last_interrupt = interrupt
        if interrupt.kind == "confirmation" and task_ids_to_reset != [str(task_id) for task_id in interrupt.task_ids]:
            if hasattr(interrupt, "model_copy"):
                last_interrupt = interrupt.model_copy(update={"task_ids": task_ids_to_reset})
        return {
            "pending_interrupt": None,
            "last_interrupt": last_interrupt,
            "tasks": state.tasks,
            "pin_verified": False,
            "last_callback": None,
        }
    return (state, interrupt)
