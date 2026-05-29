"""Airtime and data repairs for planner task normalization."""

import re

from apps.chat.src.agent.orchestrator.planning.task_planner_normalizer_parsing import (
    extract_amount_candidates,
    extract_data_plan_candidates,
    extract_network_candidates,
    extract_phone_candidates,
    parse_amount_value,
    single_unambiguous,
)
from shared.types.planner import TaskParameters
from shared.utils.network_utils import normalize_nigerian_phone, resolve_network_from_phone

_SELF_AIRTIME_PATTERNS = (
    re.compile(r"\b(my line|my number|myself|for me|na me|for myself|pour moi)\b", re.IGNORECASE),
    re.compile(r"\bbuy me\b[\w\s]{0,80}\bairtime\b", re.IGNORECASE),
)


def normalize_airtime_params(params: TaskParameters, text: str) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []
    parsed_phone: str | None = None

    if params.amount is None:
        amount, amount_ambiguous = single_unambiguous(extract_amount_candidates(text))
        if amount_ambiguous:
            ambiguous.append("amount")
        elif isinstance(amount, float):
            params.amount = amount
            patched.append("amount")

    if not params.recipient_phone:
        phone, phone_ambiguous = single_unambiguous(extract_phone_candidates(text))
        if phone_ambiguous:
            ambiguous.append("recipient_phone")
        elif isinstance(phone, str):
            parsed_phone = phone
            params.recipient_phone = phone
            patched.append("recipient_phone")
            if not params.phone:
                params.phone = phone
                patched.append("phone")

    if not params.network:
        network, network_ambiguous = single_unambiguous(extract_network_candidates(text))
        if network_ambiguous:
            ambiguous.append("network")
        elif isinstance(network, str):
            params.network = network
            patched.append("network")
        else:
            phone_for_infer = parsed_phone or params.recipient_phone
            inferred = resolve_network_from_phone(phone_for_infer or "")
            if inferred:
                params.network = inferred
                patched.append("network")

    if not params.recipient_phone and not params.is_self:
        if any(pattern.search(text) for pattern in _SELF_AIRTIME_PATTERNS):
            params.is_self = True
            patched.append("is_self")

    return patched, ambiguous


def normalize_data_params(params: TaskParameters, text: str) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []
    parsed_phone: str | None = None

    if params.amount is None:
        budget_amount = parse_amount_value(params.budget)
        if budget_amount is not None:
            params.amount = budget_amount
            patched.append("amount")
        else:
            amount, amount_ambiguous = single_unambiguous(extract_amount_candidates(text))
            if amount_ambiguous:
                ambiguous.append("amount")
            elif isinstance(amount, float):
                params.amount = amount
                patched.append("amount")

    if not params.recipient_phone and not params.phone:
        phone, phone_ambiguous = single_unambiguous(extract_phone_candidates(text))
        if phone_ambiguous:
            ambiguous.append("recipient_phone")
        elif isinstance(phone, str):
            parsed_phone = phone
            params.recipient_phone = phone
            params.phone = phone
            patched.extend(["recipient_phone", "phone"])
    elif not params.recipient_phone and params.phone:
        normalized_phone = normalize_nigerian_phone(params.phone)
        if normalized_phone:
            params.recipient_phone = normalized_phone
            patched.append("recipient_phone")
    elif params.recipient_phone and not params.phone:
        params.phone = params.recipient_phone
        patched.append("phone")

    if not params.network:
        network, network_ambiguous = single_unambiguous(extract_network_candidates(text))
        if network_ambiguous:
            ambiguous.append("network")
        elif isinstance(network, str):
            params.network = network
            patched.append("network")
        else:
            phone_for_infer = parsed_phone or params.recipient_phone or params.phone
            inferred = resolve_network_from_phone(phone_for_infer or "")
            if inferred:
                params.network = inferred
                patched.append("network")

    if not params.plan:
        plan, plan_ambiguous = single_unambiguous(extract_data_plan_candidates(text))
        if plan_ambiguous:
            ambiguous.append("plan")
        elif isinstance(plan, str):
            params.plan = plan
            patched.append("plan")

    return patched, ambiguous
