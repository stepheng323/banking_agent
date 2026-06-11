"""Batch-wide slot filling for active pre-auth transaction flows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import wave_position
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.transfers.extraction.parsers import parse_amount_input
from shared.types.planner import BatchSlotPatchDecision, BatchSlotPatchUpdate
from shared.utils.bank_aliases import display_bank_name, normalize_bank_name
from shared.utils.sanitize import normalize_bank_account_number

_ACCOUNT_RE = re.compile(r"(?P<account>(?:\d[\s,.\-]?){10,11})(?!\d)")
_LABEL_MARKER_RE = re.compile(
    r"\b(?:for|to|ti|fun|si|ga|zuwa)\s+(?P<label>[a-z][a-z0-9' .-]{0,40})",
    re.IGNORECASE,
)
_LABEL_BEFORE_ACCOUNT_RE = re.compile(
    r"(?:^|[\s,;])(?:(?:for|to|ti|fun|si|ga|zuwa)\s+)?"
    r"(?P<label>[a-z][a-z0-9' .-]{0,40}?)\s*(?:own|is|na|=|:)?\s*$",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(r"\s+\band\b\s+|[;]", re.IGNORECASE)
_BANK_STOP_RE = re.compile(r"\b(?:for|to|ti|fun|si|ga|zuwa)\b|\s+\band\b\s+|[;]", re.IGNORECASE)
_DETAIL_SIGNAL_RE = re.compile(
    r"\b(?:for|to|ti|fun|si|ga|zuwa|own| na | is |reduce|make|change|use|from|source|"
    r"narration|purpose|reason|remove|cancel)\b",
    re.IGNORECASE,
)
_AMOUNT_TOKEN_RE = re.compile(r"(?:₦|ngn)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\b")
_BATCH_AMOUNT_EDIT_RE = re.compile(r"\b(?:make|reduce|change|set)\b", re.IGNORECASE)
_ALL_BATCH_TARGET_RE = re.compile(r"\b(?:each|both|all|transactions|transfers)\b", re.IGNORECASE)
_SOURCE_CHOICE_PROMPT_RE = re.compile(r"\bwhich\s+account\s+should\s+i\s+use\b", re.IGNORECASE)
_SOURCE_CHOICE_FILLER_RE = re.compile(
    r"\b(?:use|with|from|add|choose|select|pick|my|the|please|abeg|make|it|instead|too|also|only|alone|just)\b",
    re.IGNORECASE,
)
_SOURCE_CHOICE_REPLACE_ANCHOR_RE = re.compile(
    r"\b(?:only|alone)\b|\bjust\s+(?:use\s+)?|\bfrom\b.+\bonly\b",
    re.IGNORECASE,
)
_BANK_LABEL_LEAK_RE = re.compile(
    r"\b(?:for|to|ti|fun|si|ga|zuwa)\s+[a-z][a-z0-9' .-]{0,40}(?:\s+\band\b|[,;])",
    re.IGNORECASE,
)
_TERMINAL_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED, TaskStage.EXECUTING}
_FAMILY_ALIAS_CANONICAL = {
    "mom": "mum",
    "mum": "mum",
    "mummy": "mum",
    "mother": "mum",
    "mama": "mum",
}
_PATCH_CLEAR_RECIPIENT_RESOLUTION: dict[str, Any] = {
    "recipient_bank_code": None,
    "recipient_bank_code_provider": None,
    "recipient_resolution_provider": None,
    "recipient_resolution_mode": None,
    "recipient_resolved_name": None,
    "beneficiary_id": None,
    "beneficiary_candidates": [],
    "resolved_from_saved_beneficiary": False,
    "name_mismatch": False,
    "name_match_score": None,
    "name_mismatch_warning": None,
}
_PATCH_CLEAR_FUNDING: dict[str, Any] = {
    "funding_plan": None,
    "suggested_funding_plan": None,
}


@dataclass(frozen=True)
class _AccountBankCandidate:
    account: str
    bank: str | None
    label: str | None
    evidence: str


def _normalize_label(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return _FAMILY_ALIAS_CANONICAL.get(normalized, normalized)


def _compact_label(value: str | None) -> str:
    return _normalize_label(value).replace(" ", "")


def _normalize_source_text(value: object) -> str:
    canonical = display_bank_name(str(value or "")) or str(value or "")
    return re.sub(r"[^a-z0-9]+", " ", canonical.lower()).strip()


def _source_key_candidates(value: object) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    variants = {raw, _SOURCE_CHOICE_FILLER_RE.sub(" ", raw)}
    keys: set[str] = set()
    for variant in variants:
        normalized = _normalize_source_text(variant)
        if normalized:
            keys.add(normalized)
            keys.add(normalized.replace(" ", ""))
        alias = normalize_bank_name(re.sub(r"\s+", " ", variant.lower()).strip())
        if alias:
            keys.add(alias)
            keys.add(re.sub(r"[^a-z0-9]+", "", alias))
        display = display_bank_name(variant)
        if display:
            display_key = _normalize_source_text(display)
            keys.add(display_key)
            keys.add(display_key.replace(" ", ""))
    return {key for key in keys if key}


def _current_batch_task_ids(state: OrchestratorState, interrupt: PendingInterrupt) -> list[str]:
    position = wave_position(state)
    if position.current_wave:
        task_ids = list(position.current_wave)
    else:
        task_ids = [str(task_id) for task_id in interrupt.task_ids]

    seen: set[str] = set()
    ordered: list[str] = []
    for task_id in [*task_ids, *[str(task_id) for task_id in interrupt.task_ids]]:
        if task_id in state.tasks and task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)
    return ordered


def _batch_transfer_tasks(state: OrchestratorState, interrupt: PendingInterrupt) -> list[tuple[str, TaskSpec]]:
    tasks: list[tuple[str, TaskSpec]] = []
    for task_id in _current_batch_task_ids(state, interrupt):
        task = state.tasks.get(task_id)
        if task is None or task.type != "transfer" or task.stage in _TERMINAL_STAGES:
            continue
        tasks.append((task_id, task))
    return tasks


def _bank_text(value: str | None) -> str | None:
    bank = re.sub(r"\s+", " ", (value or "").strip(" \t\r\n,.;:-")).strip()
    if not bank or bank.isdigit() or len(bank) > 40:
        return None
    if _ACCOUNT_RE.search(bank):
        return None
    bank_casefold = bank.casefold()
    if _BANK_LABEL_LEAK_RE.search(bank) and bank_casefold != "united bank for africa":
        return None
    return display_bank_name(bank) or bank


def _label_from_after(after_text: str) -> str | None:
    match = _LABEL_MARKER_RE.search(after_text)
    if match is None:
        return None
    label_text = _CLAUSE_BOUNDARY_RE.split(match.group("label"), maxsplit=1)[0]
    return label_text.strip(" \t\r\n,.;:-") or None


def _bank_from_after(after_text: str) -> str | None:
    text = after_text.strip(" \t\r\n,.;:-")
    if not text:
        return None
    bank_text = _BANK_STOP_RE.split(text, maxsplit=1)[0]
    return _bank_text(bank_text)


def _label_from_before(before_text: str) -> str | None:
    tail = _CLAUSE_BOUNDARY_RE.split(before_text)[-1].strip(" \t\r\n,.;:-")
    if not tail:
        return None
    match = _LABEL_BEFORE_ACCOUNT_RE.search(tail)
    if match is None:
        return None
    label = match.group("label").strip(" \t\r\n,.;:-")
    if not label or label.isdigit():
        return None
    return label


def _extract_account_bank_candidates(text: str) -> tuple[list[_AccountBankCandidate], int]:
    matches = list(_ACCOUNT_RE.finditer(text))
    candidates: list[_AccountBankCandidate] = []
    invalid_count = 0
    for index, match in enumerate(matches):
        account = normalize_bank_account_number(match.group("account"))
        if len(account) != 10:
            invalid_count += 1
            continue
        previous_end = matches[index - 1].end() if index > 0 else 0
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        before = text[previous_end : match.start()]
        after = text[match.end() : next_start]
        label = _label_from_after(after) or _label_from_before(before)
        bank = _bank_from_after(after)
        if bank is None:
            invalid_count += 1
            continue
        evidence = text[previous_end:next_start].strip()
        candidates.append(_AccountBankCandidate(account=account, bank=bank, label=label, evidence=evidence))
    return candidates, invalid_count


def _task_label_values(task: TaskSpec) -> list[str]:
    labels = [
        task.payload.get("recipient_name"),
        task.payload.get("recipient_resolved_name"),
    ]
    return [str(label).strip() for label in labels if isinstance(label, str) and label.strip()]


def _resolve_task_for_label(
    label: str | None,
    tasks: list[tuple[str, TaskSpec]],
) -> str | None:
    normalized_label = _normalize_label(label)
    compact_label = _compact_label(label)
    if not normalized_label:
        return None

    exact_matches: list[str] = []
    prefix_matches: list[str] = []
    for task_id, task in tasks:
        for task_label in _task_label_values(task):
            normalized_task_label = _normalize_label(task_label)
            compact_task_label = _compact_label(task_label)
            if normalized_task_label == normalized_label:
                exact_matches.append(task_id)
            elif (
                compact_label
                and compact_task_label
                and (compact_task_label.startswith(compact_label) or compact_label.startswith(compact_task_label))
            ):
                prefix_matches.append(task_id)

    exact_unique = sorted(set(exact_matches))
    if len(exact_unique) == 1:
        return exact_unique[0]
    prefix_unique = sorted(set(prefix_matches))
    if len(prefix_unique) == 1:
        return prefix_unique[0]
    return None


def _focused_task_id(interrupt: PendingInterrupt, tasks: list[tuple[str, TaskSpec]]) -> str | None:
    task_ids = [str(task_id) for task_id in interrupt.task_ids]
    task_id_set = {task_id for task_id, _task in tasks}
    focused = [task_id for task_id in task_ids if task_id in task_id_set]
    if len(focused) == 1:
        return focused[0]
    if len(tasks) == 1:
        return tasks[0][0]
    return None


def _recipient_detail_patch(*, account: str, bank: str) -> dict[str, Any]:
    return {
        "recipient_account": account,
        "recipient_account_number": account,
        "recipient_bank_name": bank,
        "confirmation": {"confirmed": False},
        **_PATCH_CLEAR_RECIPIENT_RESOLUTION,
        **_PATCH_CLEAR_FUNDING,
    }


def _merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> None:
    target.update(patch)


def _contextual_amount_from_token(token: re.Match[str], tasks: list[tuple[str, TaskSpec]]) -> Decimal | None:
    raw_amount = token.group("amount")
    suffix = token.group("suffix").lower()
    parsed = parse_amount_input(f"{raw_amount}{suffix}")
    if parsed is None:
        return None
    if not suffix and parsed < 1000:
        existing_amounts: list[Decimal] = []
        for _task_id, task in tasks:
            raw_existing = task.payload.get("amount")
            if raw_existing is None:
                continue
            try:
                existing_amounts.append(Decimal(str(raw_existing)))
            except Exception:
                continue
        if existing_amounts and all(amount >= 1000 for amount in existing_amounts):
            parsed *= 1000
    try:
        amount = Decimal(str(parsed))
    except Exception:
        return None
    return amount if amount > 0 else None


def _amount_patch(value: Any) -> dict[str, Any] | None:
    amount: Any = value
    if isinstance(value, str):
        amount = parse_amount_input(value)
    if amount is None:
        return None
    try:
        decimal_amount = Decimal(str(amount))
    except Exception:
        return None
    if decimal_amount <= 0:
        return None
    return {
        "amount": decimal_amount,
        "transfer_all": False,
        "transfer_percentage": None,
        "suggested_amount": None,
        "confirmation": {"confirmed": False},
        **_PATCH_CLEAR_FUNDING,
    }


def _deterministic_amount_overrides(
    *,
    text: str,
    tasks: list[tuple[str, TaskSpec]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    if len(tasks) < 2 or not _BATCH_AMOUNT_EDIT_RE.search(text):
        return {}, False

    amount_values = [
        amount
        for match in _AMOUNT_TOKEN_RE.finditer(text)
        if (amount := _contextual_amount_from_token(match, tasks)) is not None
    ]
    if not amount_values:
        return {}, False

    if len(amount_values) == len(tasks):
        return {
            task_id: _amount_patch(amount) or {} for (task_id, _task), amount in zip(tasks, amount_values, strict=True)
        }, True

    if len(amount_values) == 1 and _ALL_BATCH_TARGET_RE.search(text):
        amount = amount_values[0]
        return {task_id: _amount_patch(amount) or {} for task_id, _task in tasks}, True

    return {}, False


def _deterministic_batch_slot_overrides(
    *,
    text: str,
    interrupt: PendingInterrupt,
    tasks: list[tuple[str, TaskSpec]],
) -> tuple[dict[str, dict[str, Any]], bool, bool]:
    amount_overrides, amount_confident = _deterministic_amount_overrides(text=text, tasks=tasks)
    if amount_confident and amount_overrides:
        return amount_overrides, True, False

    candidates, invalid_candidate_count = _extract_account_bank_candidates(text)
    if not candidates:
        return {}, False, invalid_candidate_count > 0

    overrides: dict[str, dict[str, Any]] = {}
    unresolved = 0
    focused_task_id = _focused_task_id(interrupt, tasks)

    for candidate in candidates:
        task_id = _resolve_task_for_label(candidate.label, tasks)
        if task_id is None and len(candidates) == 1 and focused_task_id is not None:
            task_id = focused_task_id
        if task_id is None or candidate.bank is None:
            unresolved += 1
            continue
        if task_id in overrides:
            unresolved += 1
            continue
        overrides[task_id] = _recipient_detail_patch(account=candidate.account, bank=candidate.bank)

    unsafe_or_incomplete = invalid_candidate_count > 0
    complete_confident = bool(
        candidates
        and invalid_candidate_count == 0
        and unresolved == 0
        and len(overrides) == len(candidates)
    )
    return overrides, complete_confident, unsafe_or_incomplete


def _narration_patch(value: Any) -> dict[str, Any] | None:
    narration = str(value or "").strip(" \t\r\n,.;:-")
    if not narration:
        return None
    return {
        "authored_narration": narration,
        "narration": narration,
        "user_note": narration,
        "confirmation": {"confirmed": False},
    }


def _source_patch(update: BatchSlotPatchUpdate) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    source_bank = str(update.source_bank_name or "").strip()
    if source_bank:
        patch.update(
            {
                "source_bank_name": source_bank,
                "source_account_id": None,
                "source_account_index": None,
                "source_affinity_mode": "explicit",
                "confirmation": {"confirmed": False},
                **_PATCH_CLEAR_FUNDING,
            }
        )
    if update.source_accounts is not None:
        sources = [str(source).strip() for source in update.source_accounts if str(source).strip()]
        patch.update(
            {
                "source_accounts": sources,
                "use_dual_accounts": bool(sources) or update.use_dual_accounts,
                "source_affinity_mode": "explicit",
                "confirmation": {"confirmed": False},
                **_PATCH_CLEAR_FUNDING,
            }
        )
    elif update.use_dual_accounts is not None:
        patch.update(
            {
                "use_dual_accounts": update.use_dual_accounts,
                "confirmation": {"confirmed": False},
                **_PATCH_CLEAR_FUNDING,
            }
        )
    return patch


def _loaded_account_bank(account: dict[str, Any]) -> str | None:
    return _bank_text(str(account.get("bank_name") or account.get("bank") or ""))


def _is_default_account(account: dict[str, Any]) -> bool:
    return bool(account.get("is_default"))


def _account_last4(account: dict[str, Any]) -> str:
    return "".join(ch for ch in str(account.get("account_number") or account.get("number") or "") if ch.isdigit())[-4:]


def _matches_source_choice(text: str, account: dict[str, Any]) -> bool:
    text_keys = _source_key_candidates(text)
    if not text_keys:
        return False
    bank = _loaded_account_bank(account)
    bank_keys = _source_key_candidates(bank)
    if bank_keys and (text_keys & bank_keys):
        return True
    if any(bank_key in text_key for bank_key in bank_keys for text_key in text_keys):
        return True
    last4 = _account_last4(account)
    digits = "".join(ch for ch in text if ch.isdigit())
    return bool(last4 and digits and last4 in digits)


def _source_choice_replaces_anchor(text: str) -> bool:
    return bool(_SOURCE_CHOICE_REPLACE_ANCHOR_RE.search(text))


def _anchor_accounts_from_interrupt(
    *,
    interrupt: PendingInterrupt,
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metadata = getattr(interrupt, "metadata", {}) or {}
    anchor_ids = [str(source_id) for source_id in metadata.get("anchor_source_ids", []) if str(source_id).strip()]
    if not anchor_ids:
        return [account for account in accounts if _is_default_account(account)]
    by_id = {str(account.get("id")): account for account in accounts if account.get("id")}
    return [by_id[source_id] for source_id in anchor_ids if source_id in by_id]


def _batch_source_choice_overrides(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    text: str,
    tasks: list[tuple[str, TaskSpec]],
) -> dict[str, dict[str, Any]]:
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    source_choice_fields = any(
        "source_accounts" in [str(field) for field in fields]
        for fields in fields_by_task.values()
        if isinstance(fields, list)
    )
    if not source_choice_fields and not _SOURCE_CHOICE_PROMPT_RE.search(str(interrupt.prompt or "")):
        return {}

    accounts = loaded_context(state).transaction_account_rows_or_account_rows
    if not accounts:
        return {}

    matched = [account for account in accounts if _matches_source_choice(text, account)]
    if len(matched) != 1:
        return {}

    selected_bank = _loaded_account_bank(matched[0])
    if not selected_bank:
        return {}

    selected_bank_key = _normalize_source_text(selected_bank)
    source_accounts: list[str] = []
    replaces_anchor = _source_choice_replaces_anchor(text)
    if not replaces_anchor:
        for account in _anchor_accounts_from_interrupt(interrupt=interrupt, accounts=accounts):
            anchor_bank = _loaded_account_bank(account)
            if anchor_bank and _normalize_source_text(anchor_bank) != selected_bank_key:
                source_accounts.append(anchor_bank)
    source_accounts.append(selected_bank)

    patch = {
        "source_accounts": source_accounts,
        "use_dual_accounts": len(source_accounts) > 1,
        "source_pooling_locked": replaces_anchor,
        "source_account_id": None,
        "source_account_index": None,
        "source_bank_name": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
        "suggested_funding_plan": None,
        "confirmation": {"confirmed": False},
    }
    return {task_id: dict(patch) for task_id, _task in tasks}


def _patch_from_semantic_update(update: BatchSlotPatchUpdate) -> dict[str, Any] | None:
    patch: dict[str, Any] = {}
    if update.recipient_account is not None:
        account = normalize_bank_account_number(str(update.recipient_account))
        if len(account) != 10:
            return None
        bank = _bank_text(update.recipient_bank_name)
        if not bank:
            return None
        _merge_patch(patch, _recipient_detail_patch(account=account, bank=bank))
    elif update.recipient_bank_name is not None:
        bank = _bank_text(update.recipient_bank_name)
        if not bank:
            return None
        _merge_patch(
            patch,
            {
                "recipient_bank_name": bank,
                "confirmation": {"confirmed": False},
                "recipient_bank_code": None,
                "recipient_bank_code_provider": None,
                "recipient_resolution_provider": None,
                "recipient_resolution_mode": None,
                "recipient_resolved_name": None,
                **_PATCH_CLEAR_FUNDING,
            },
        )

    amount_patch = _amount_patch(update.amount)
    if amount_patch is not None:
        _merge_patch(patch, amount_patch)

    narration_patch = _narration_patch(update.narration)
    if narration_patch is not None:
        _merge_patch(patch, narration_patch)

    _merge_patch(patch, _source_patch(update))
    return patch or None


def _semantic_overrides(
    *,
    decision: BatchSlotPatchDecision,
    tasks: list[tuple[str, TaskSpec]],
) -> tuple[dict[str, dict[str, Any]], int]:
    overrides: dict[str, dict[str, Any]] = {}
    invalid_count = 0
    for update in decision.updates:
        task_id = (
            update.target_task_id
            if update.target_task_id and any(tid == update.target_task_id for tid, _ in tasks)
            else None
        )
        if task_id is None:
            for target_text in update.target_texts:
                task_id = _resolve_task_for_label(target_text, tasks)
                if task_id is not None:
                    break
        if task_id is None:
            invalid_count += 1
            continue
        patch = _patch_from_semantic_update(update)
        if patch is None:
            invalid_count += 1
            continue
        merged = overrides.setdefault(task_id, {})
        _merge_patch(merged, patch)
    return overrides, invalid_count


def _batch_slot_context_json(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    tasks: list[tuple[str, TaskSpec]],
) -> str:
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    context = {
        "interrupt_kind": interrupt.kind,
        "prompt": interrupt.prompt,
        "tasks": [
            {
                "task_id": task_id,
                "type": task.type,
                "stage": str(task.stage),
                "recipient_name": task.payload.get("recipient_name"),
                "recipient_resolved_name": task.payload.get("recipient_resolved_name"),
                "amount": str(task.payload.get("amount")) if task.payload.get("amount") is not None else None,
                "has_recipient_account": bool(task.payload.get("recipient_account")),
                "has_recipient_bank_name": bool(task.payload.get("recipient_bank_name")),
                "required_fields": fields_by_task.get(task_id, []),
            }
            for task_id, task in tasks
        ],
    }
    del state
    return json.dumps(context, ensure_ascii=True, separators=(",", ":"))


def _looks_like_batch_slot_text(text: str) -> bool:
    return bool(_ACCOUNT_RE.search(text) or _DETAIL_SIGNAL_RE.search(f" {text} "))


def _invalid_slot_clarification() -> str:
    return (
        "Please send each recipient's account number with only the bank name. "
        "For example: 8067892221 Wema for mum and 8080844362 Opay for ay."
    )


async def _semantic_batch_slot_overrides(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
    tasks: list[tuple[str, TaskSpec]],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    task_planner = runtime.task_planner
    if task_planner is None or not hasattr(task_planner, "interpret_batch_slot_patch"):
        return {}, None
    decision = await task_planner.interpret_batch_slot_patch(
        runtime.state_view.phone_number,
        runtime.text,
        context=_batch_slot_context_json(state=state, interrupt=runtime.interrupt, tasks=tasks),
        path_label="interrupt_path",
    )
    if not isinstance(decision, BatchSlotPatchDecision):
        decision = BatchSlotPatchDecision.model_validate(decision)
    logger.info(
        "batch_slot_patch_decision",
        confidence=decision.confidence,
        update_count=len(decision.updates),
        needs_clarification=decision.needs_clarification,
        reason=decision.reason,
    )
    if decision.confidence < 0.72:
        return {}, None
    overrides, invalid_count = _semantic_overrides(decision=decision, tasks=tasks)
    if invalid_count:
        clarification = (
            decision.clarification
            if decision.needs_clarification and decision.clarification
            else _invalid_slot_clarification()
        )
        return {}, clarification
    if overrides:
        return overrides, None
    if decision.needs_clarification and decision.clarification:
        return {}, decision.clarification
    return {}, None


def _clarification_updates(state: OrchestratorState, interrupt: PendingInterrupt, clarification: str) -> dict[str, Any]:
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": interrupt_state_view(state).tasks,
        "outbox": [{"type": "say", "text": clarification}],
    }


async def _resolve_batch_slot_fill_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if getattr(interrupt, "kind", None) not in {"input", "confirmation"}:
        return None
    if not runtime.text.strip():
        return None

    tasks = _batch_transfer_tasks(state, interrupt)
    if len(tasks) < 2:
        return None

    source_choice_overrides = _batch_source_choice_overrides(
        state=state,
        interrupt=interrupt,
        text=runtime.text,
        tasks=tasks,
    )
    if source_choice_overrides:
        logger.info(
            "batch_source_choice_fastpath_applied",
            task_ids=sorted(source_choice_overrides.keys()),
        )
        return _continue_flow_updates(state, interrupt, precomputed_payload_overrides=source_choice_overrides)

    if not _looks_like_batch_slot_text(runtime.text):
        return None

    deterministic_overrides, complete_confident, unsafe_deterministic_parse = _deterministic_batch_slot_overrides(
        text=runtime.text,
        interrupt=interrupt,
        tasks=tasks,
    )
    if complete_confident and deterministic_overrides:
        logger.info(
            "batch_slot_fastpath_applied",
            task_ids=sorted(deterministic_overrides.keys()),
            slot_count=sum(len(patch) for patch in deterministic_overrides.values()),
        )
        return _continue_flow_updates(state, interrupt, precomputed_payload_overrides=deterministic_overrides)

    semantic_overrides, clarification = await _semantic_batch_slot_overrides(state=state, runtime=runtime, tasks=tasks)
    if semantic_overrides:
        logger.info(
            "batch_slot_semantic_patch_applied",
            task_ids=sorted(semantic_overrides.keys()),
            slot_count=sum(len(patch) for patch in semantic_overrides.values()),
        )
        return _continue_flow_updates(state, interrupt, precomputed_payload_overrides=semantic_overrides)
    if clarification:
        logger.info("batch_slot_semantic_clarification", task_ids=[task_id for task_id, _ in tasks])
        return _clarification_updates(state, interrupt, clarification)
    if unsafe_deterministic_parse:
        logger.info("batch_slot_invalid_deterministic_parse", task_ids=[task_id for task_id, _ in tasks])
        return _clarification_updates(state, interrupt, _invalid_slot_clarification())
    return None


__all__ = ["_resolve_batch_slot_fill_updates"]
