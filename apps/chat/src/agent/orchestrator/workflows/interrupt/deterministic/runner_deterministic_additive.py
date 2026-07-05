"""Deterministic additive transaction switches during pending confirmations."""

import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _is_obvious_airtime_request,
    _is_obvious_data_request,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _next_interrupt_task_id, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_updates import _switch_updates
from banking.transfers.extraction.parsers import parse_amount_input
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

_AMOUNT_TOKEN_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b", re.IGNORECASE)
_DATA_SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:gb|mb)\b", re.IGNORECASE)
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_SELF_TARGET_RE = re.compile(
    r"\b(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)\b",
    re.IGNORECASE,
)
_ADDITIVE_CUE_RE = re.compile(r"\b(?:also|add|plus|and\s+(?:also\s+)?)\b", re.IGNORECASE)
_PENDING_EDIT_CUE_RE = re.compile(
    r"\b(?:make|change|update|switch|replace|increase|reduce|set|correct|instead|i\s+said)\b",
    re.IGNORECASE,
)
_EXPLICIT_AIRTIME_DOMAIN_RE = re.compile(r"\bairtime\b", re.IGNORECASE)
_EXPLICIT_DATA_DOMAIN_RE = re.compile(r"\b(?:data|bundle)\b|\b\d+(?:\.\d+)?\s*(?:gb|mb)\b", re.IGNORECASE)


def _first_amount(text: str) -> float | None:
    for match in _AMOUNT_TOKEN_RE.finditer(text):
        amount = parse_amount_input(match.group(0))
        if amount is not None:
            return amount
    return None


def _first_phone(text: str) -> str | None:
    for candidate in _PHONE_CANDIDATE_PATTERN.findall(text):
        phone = normalize_nigerian_phone(candidate)
        if phone:
            return phone
    return None


def _first_network(text: str) -> str | None:
    for token in re.findall(r"[A-Za-z0-9]+", text):
        network = normalize_network_name(token)
        if network:
            return network
    return None


def _data_plan_name(text: str) -> str | None:
    match = _DATA_SIZE_RE.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(0)).upper()


def _is_self_target(text: str, phone: str | None) -> bool:
    return phone is None and bool(_SELF_TARGET_RE.search(text))


def _has_explicit_additive_domain(text: str, target_intent: str) -> bool:
    if target_intent == "airtime":
        return bool(_EXPLICIT_AIRTIME_DOMAIN_RE.search(text))
    if target_intent == "data":
        return bool(_EXPLICIT_DATA_DOMAIN_RE.search(text))
    return False


def _looks_like_pending_confirmation_edit(text: str) -> bool:
    return bool(_PENDING_EDIT_CUE_RE.search(text))


def _deterministic_additive_payload(text: str) -> tuple[str, dict[str, Any]] | None:
    if _is_obvious_airtime_request(text):
        amount = _first_amount(text)
        phone = _first_phone(text)
        is_self = _is_self_target(text, phone)
        if amount is None or (not phone and not is_self):
            return None
        payload: dict[str, Any] = {
            "action": "buy_airtime",
            "amount": amount,
            "is_self": is_self,
            "skip_extraction": True,
        }
        if phone:
            payload["recipient_phone"] = phone
        if network := _first_network(text):
            payload["network"] = network
        return "airtime", payload

    if _is_obvious_data_request(text):
        plan_name = _data_plan_name(text)
        phone = _first_phone(text)
        is_self = _is_self_target(text, phone)
        if plan_name is None or (not phone and not is_self):
            return None
        payload = {
            "action": "buy_data",
            "plan": plan_name,
            "plan_name": plan_name,
            "is_self": is_self,
            "skip_extraction": True,
        }
        if phone:
            payload["target_phone"] = phone
        if network := _first_network(text):
            payload["network"] = network
        return "data", payload

    return None


def _deterministic_additive_transaction_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if getattr(interrupt, "kind", None) != "confirmation":
        return None
    if not runtime.current_task_types or not runtime.current_task_types.issubset(TRANSACTION_INTENTS):
        return None

    parsed = _deterministic_additive_payload(runtime.text)
    if parsed is None:
        return None
    if _looks_like_pending_confirmation_edit(runtime.text):
        return None

    target_intent, payload = parsed
    has_additive_cue = bool(_ADDITIVE_CUE_RE.search(runtime.text))
    if not has_additive_cue:
        if target_intent in runtime.current_task_types:
            return None
        if not _has_explicit_additive_domain(runtime.text, target_intent):
            return None

    task_id = _next_interrupt_task_id(state=state, target_intent=target_intent)
    payload = {
        "instruction": runtime.text,
        "message": runtime.text,
        **payload,
    }
    task = TaskSpec(
        id=task_id,
        type=cast(Any, target_intent),
        depends_on=[],
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    logger.info(
        "interrupt_deterministic_additive_transaction",
        kind=interrupt.kind,
        active_type=runtime.active_type,
        target_intent=target_intent,
        task_id=task_id,
    )
    return _switch_updates(
        state=state,
        interrupt=interrupt,
        active_type=runtime.active_type,
        current_task_types=runtime.current_task_types,
        new_tasks={task_id: task},
        waves=[[task_id]],
        new_task_types={target_intent},
        text=runtime.text,
        planner_output=None,
        primary_intent=target_intent,
        merge_with_pending_confirmation=True,
    )


__all__ = ["_deterministic_additive_transaction_updates"]
