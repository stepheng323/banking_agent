"""Deterministic post-processing for planner transaction tasks.

This layer improves one-shot extraction completeness without extra LLM calls.
It only patches missing transaction parameters when parsing is unambiguous.
"""

from __future__ import annotations

import re
from typing import Any

from shared.types.planner import PlannedTask, PlannerOutput, RecipientAllocation, TaskParameters
from shared.utils.bank_aliases import BANK_ALIASES, normalize_bank_name
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone, resolve_network_from_phone
from shared.utils.sanitize import normalize_bank_account_number

logger = get_logger(__name__)

_TX_EXECUTORS = {"transfer", "airtime", "data"}
_PHONE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_ACCOUNT_PATTERN = re.compile(r"(?:\d[\s,.\-]?){10,11}")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_AMOUNT_TOKEN_PATTERN = re.compile(
    r"(?<!\d)(?:₦|ngn)?\s*(?P<number>\d[\d,]*(?:\.\d+)?)(?P<suffix>[kKmMhH]?)(?!\d)"
)
_DATA_PLAN_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(gb|mb)(?!\w)", re.IGNORECASE)
_SELF_AIRTIME_PATTERNS = (
    re.compile(r"\b(my line|my number|myself|for me|na me|for myself|pour moi)\b", re.IGNORECASE),
    re.compile(r"\bbuy me\b[\w\s]{0,80}\bairtime\b", re.IGNORECASE),
)
_CANONICAL_NETWORKS = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_CANONICAL_BANK_DISPLAY = {
    "gtbank": "GTBank",
    "uba": "UBA",
    "firstbank": "First Bank",
    "fcmb": "FCMB",
    "stanbic": "Stanbic",
    "ecobank": "Ecobank",
    "fidelity": "Fidelity",
    "wema": "Wema",
    "polaris": "Polaris",
    "keystone": "Keystone",
    "union": "Union",
    "sterling": "Sterling",
    "providus": "Providus",
    "opay": "Opay",
    "palmpay": "PalmPay",
    "kuda": "Kuda",
    "moniepoint": "Moniepoint",
    "access": "Access Bank",
}
_BANK_ALIASES = sorted(
    {alias for alias in (list(BANK_ALIASES.keys()) + list(BANK_ALIASES.values())) if alias and len(alias) >= 3},
    key=len,
    reverse=True,
)


def _digits_only(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _extract_account_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in _ACCOUNT_PATTERN.findall(text):
        normalized = normalize_bank_account_number(raw)
        if not normalized:
            continue
        if len(normalized) not in {10, 11}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _extract_phone_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in _PHONE_PATTERN.findall(text):
        normalized = normalize_nigerian_phone(raw)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _extract_network_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_PATTERN.findall(text):
        normalized = normalize_network_name(token)
        if not normalized and token.strip().upper() in _CANONICAL_NETWORKS:
            normalized = token.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _extract_amount_candidates(text: str) -> list[float]:
    candidates: list[float] = []
    seen: set[float] = set()
    for match in _AMOUNT_TOKEN_PATTERN.finditer(text):
        num_text = match.group("number").replace(",", "")
        suffix = (match.group("suffix") or "").lower()
        try:
            numeric = float(num_text)
        except ValueError:
            continue
        if numeric <= 0:
            continue

        # Precision-first: avoid interpreting long account numbers as amount.
        digits = _digits_only(num_text)
        has_currency_or_suffix = "₦" in match.group(0) or "ngn" in match.group(0).lower() or bool(suffix)
        if not has_currency_or_suffix and len(digits) >= 9:
            continue

        multiplier = 1.0
        if suffix == "k":
            multiplier = 1000.0
        elif suffix == "h":
            multiplier = 100.0
        elif suffix == "m":
            multiplier = 1_000_000.0

        amount = numeric * multiplier
        if amount in seen:
            continue
        seen.add(amount)
        candidates.append(amount)
    return candidates


def _extract_data_plan_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for match in _DATA_PLAN_PATTERN.finditer(text):
        amount = match.group(1)
        unit = match.group(2).upper()
        value = f"{amount}{unit}"
        if value in seen:
            continue
        seen.add(value)
        candidates.append(value)
    return candidates


def _extract_bank_candidates(text: str) -> list[str]:
    lowered = text.lower()
    canonical_hits: dict[str, str] = {}
    for alias in _BANK_ALIASES:
        pattern = r"\b" + re.escape(alias).replace("\\ ", r"\s+") + r"\b"
        if not re.search(pattern, lowered):
            continue
        canonical = normalize_bank_name(alias)
        if canonical in canonical_hits:
            continue
        canonical_hits[canonical] = alias

    results: list[str] = []
    for canonical, alias in canonical_hits.items():
        display = _CANONICAL_BANK_DISPLAY.get(canonical)
        if display:
            results.append(display)
        else:
            results.append(alias.title())
    return results


def _single_unambiguous(values: list[Any]) -> tuple[Any | None, bool]:
    """Return (value, ambiguous)."""
    if not values:
        return None, False
    if len(values) > 1:
        return None, True
    return values[0], False


def _parse_amount_value(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    single, ambiguous = _single_unambiguous(_extract_amount_candidates(raw))
    if ambiguous:
        return None
    if isinstance(single, float):
        return single
    try:
        return float(raw)
    except ValueError:
        return None


def _normalize_transfer_params(params: TaskParameters, text: str) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []

    account_ambiguous = False
    if not params.recipient_account:
        account, account_ambiguous = _single_unambiguous(_extract_account_candidates(text))
        if account_ambiguous:
            ambiguous.append("recipient_account")
        elif isinstance(account, str):
            params.recipient_account = account
            patched.append("recipient_account")

    # Keep account+bank pairing strict for transfer destination.
    if not params.bank_name and not account_ambiguous:
        bank_name, bank_ambiguous = _single_unambiguous(_extract_bank_candidates(text))
        if bank_ambiguous:
            ambiguous.append("bank_name")
        elif isinstance(bank_name, str):
            params.bank_name = bank_name
            patched.append("bank_name")

    if params.amount is None:
        amount, amount_ambiguous = _single_unambiguous(_extract_amount_candidates(text))
        if amount_ambiguous:
            ambiguous.append("amount")
        elif isinstance(amount, float):
            params.amount = amount
            patched.append("amount")

    return patched, ambiguous


def _normalize_airtime_params(params: TaskParameters, text: str) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []
    parsed_phone: str | None = None

    if params.amount is None:
        amount, amount_ambiguous = _single_unambiguous(_extract_amount_candidates(text))
        if amount_ambiguous:
            ambiguous.append("amount")
        elif isinstance(amount, float):
            params.amount = amount
            patched.append("amount")

    if not params.recipient_phone:
        phone, phone_ambiguous = _single_unambiguous(_extract_phone_candidates(text))
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
        network, network_ambiguous = _single_unambiguous(_extract_network_candidates(text))
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


def _normalize_data_params(params: TaskParameters, text: str) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []
    parsed_phone: str | None = None

    if params.amount is None:
        budget_amount = _parse_amount_value(params.budget)
        if budget_amount is not None:
            params.amount = budget_amount
            patched.append("amount")
        else:
            amount, amount_ambiguous = _single_unambiguous(_extract_amount_candidates(text))
            if amount_ambiguous:
                ambiguous.append("amount")
            elif isinstance(amount, float):
                params.amount = amount
                patched.append("amount")

    if not params.recipient_phone and not params.phone:
        phone, phone_ambiguous = _single_unambiguous(_extract_phone_candidates(text))
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
        network, network_ambiguous = _single_unambiguous(_extract_network_candidates(text))
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
        plan, plan_ambiguous = _single_unambiguous(_extract_data_plan_candidates(text))
        if plan_ambiguous:
            ambiguous.append("plan")
        elif isinstance(plan, str):
            params.plan = plan
            patched.append("plan")

    return patched, ambiguous


def _has_batch_transfer_cue(user_text: str) -> bool:
    lowered = (user_text or "").lower()
    return any(cue in lowered for cue in (" each ", " split ", " between ", " btw "))


def _single_recipient_allocation(params: TaskParameters) -> RecipientAllocation | None:
    allocations = params.recipient_allocations or []
    if len(allocations) != 1:
        return None
    allocation = allocations[0]
    recipient_name = str(allocation.recipient_name or "").strip()
    amount = float(allocation.amount or 0)
    if not recipient_name or amount <= 0:
        return None
    return RecipientAllocation(recipient_name=recipient_name, amount=amount)


def _collapse_transfer_target(params: TaskParameters) -> tuple[str, float] | None:
    allocation = _single_recipient_allocation(params)
    if allocation is not None:
        return allocation.recipient_name, allocation.amount

    recipient_name = str(params.recipient_name or params.recipient or "").strip()
    amount = _parse_amount_value(params.amount)
    if not recipient_name or amount is None or amount <= 0:
        return None
    return recipient_name, amount


def _can_collapse_transfer_task(task: PlannedTask) -> bool:
    if task.executor != "transfer" or task.action != "send_money" or task.depends_on:
        return False

    params = task.parameters or TaskParameters()
    if params.transfer_all or params.transfer_percentage is not None:
        return False
    if params.reference is not None:
        return False
    if (
        params.explicit_split
        or params.source_accounts
        or (params.recipient_allocations and len(params.recipient_allocations) != 1)
    ):
        return False
    if params.recipient_account or params.bank_name or params.recipient_phone or params.phone:
        return False
    if (
        params.network
        or params.plan
        or params.schedule
        or params.scheduled
        or params.schedule_id
        or params.schedule_selector
    ):
        return False
    if params.recurring or params.international or params.alias or params.is_self:
        return False
    if params.source_bank_name or params.source_account_index is not None or params.use_dual_accounts is not None:
        return False

    return _collapse_transfer_target(params) is not None


def _collapse_transfer_batch_tasks(planner_output: PlannerOutput, user_text: str) -> PlannerOutput:
    if not _has_batch_transfer_cue(user_text):
        return planner_output

    tasks = planner_output.tasks
    if len(tasks) < 2:
        return planner_output

    collapsed_tasks: list[PlannedTask] = []
    id_rewrites: dict[str, str] = {}
    applied_count = 0
    idx = 0

    while idx < len(tasks):
        task = tasks[idx]
        if not _can_collapse_transfer_task(task):
            collapsed_tasks.append(task)
            idx += 1
            continue

        run_end = idx + 1
        while run_end < len(tasks) and _can_collapse_transfer_task(tasks[run_end]):
            run_end += 1

        run = tasks[idx:run_end]
        if len(run) < 2:
            collapsed_tasks.append(task)
            idx = run_end
            continue

        base_task = run[0].model_copy(deep=True)
        allocations: list[RecipientAllocation] = []
        total_amount = 0.0
        for child in run:
            params = child.parameters or TaskParameters()
            target = _collapse_transfer_target(params)
            if target is None:
                allocations = []
                break
            recipient_name, amount = target
            allocations.append(RecipientAllocation(recipient_name=recipient_name, amount=amount))
            total_amount += amount

        if len(allocations) != len(run):
            collapsed_tasks.extend(run)
            idx = run_end
            continue

        base_params = base_task.parameters.model_copy(deep=True) if base_task.parameters else TaskParameters()
        base_params.recipient = None
        base_params.recipient_name = None
        base_params.amount = total_amount
        base_params.recipient_allocations = allocations
        base_task.parameters = base_params
        base_task.instruction = user_text

        collapsed_tasks.append(base_task)
        for child in run[1:]:
            id_rewrites[child.task_id] = base_task.task_id
        applied_count += 1
        idx = run_end

    if not applied_count:
        return planner_output

    rewritten_tasks: list[PlannedTask] = []
    for task in collapsed_tasks:
        if not task.depends_on:
            rewritten_tasks.append(task)
            continue
        updated_depends_on: list[str] = []
        seen: set[str] = set()
        for dep in task.depends_on:
            rewritten = id_rewrites.get(dep, dep)
            if rewritten in seen:
                continue
            seen.add(rewritten)
            updated_depends_on.append(rewritten)
        rewritten_tasks.append(task.model_copy(update={"depends_on": updated_depends_on}))

    logger.info(
        "planner_transfer_batch_collapsed_to_allocations",
        collapsed_run_count=applied_count,
        collapsed_task_count=len(id_rewrites) + applied_count,
        locale=planner_output.detected_language or "unknown",
    )
    return planner_output.model_copy(update={"tasks": rewritten_tasks})


def normalize_planner_transaction_output(planner_output: PlannerOutput, user_text: str) -> PlannerOutput:
    """Patch missing transaction parameters with deterministic, precision-first parsing."""
    if not planner_output.tasks:
        return planner_output

    locale = planner_output.detected_language or "unknown"
    updated_tasks = []
    applied_count = 0

    for task in planner_output.tasks:
        if task.executor not in _TX_EXECUTORS:
            updated_tasks.append(task)
            continue

        source_text = (task.instruction or user_text or "").strip()
        if not source_text:
            updated_tasks.append(task)
            continue

        params = task.parameters.model_copy(deep=True) if task.parameters else TaskParameters()
        patched_fields: list[str] = []
        ambiguous_fields: list[str] = []

        if task.executor == "transfer":
            patched_fields, ambiguous_fields = _normalize_transfer_params(params, source_text)
        elif task.executor == "airtime":
            patched_fields, ambiguous_fields = _normalize_airtime_params(params, source_text)
        elif task.executor == "data":
            patched_fields, ambiguous_fields = _normalize_data_params(params, source_text)

        if patched_fields:
            applied_count += 1
            logger.info(
                "planner_task_normalizer_applied",
                task_id=task.task_id,
                executor=task.executor,
                locale=locale,
                normalized_fields=patched_fields,
            )
            updated_tasks.append(task.model_copy(update={"parameters": params}))
        else:
            updated_tasks.append(task)

        if ambiguous_fields:
            logger.info(
                "planner_task_normalizer_ambiguous_skip",
                task_id=task.task_id,
                executor=task.executor,
                locale=locale,
                ambiguous_fields=ambiguous_fields,
            )

    if applied_count:
        logger.info(
            "planner_task_normalizer_summary",
            locale=locale,
            normalized_task_count=applied_count,
            total_tasks=len(planner_output.tasks),
        )

    normalized_output = planner_output.model_copy(update={"tasks": updated_tasks})
    return _collapse_transfer_batch_tasks(normalized_output, user_text)


__all__ = ["normalize_planner_transaction_output"]
