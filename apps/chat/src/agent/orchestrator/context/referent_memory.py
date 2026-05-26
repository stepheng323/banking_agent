"""Canonical short-term referent memory for safe follow-up grounding."""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType

ReferentType = Literal["beneficiary", "recipient", "phone", "amount", "source_account", "transaction", "data_plan"]
ReferentSource = Literal["active_flow", "context_frame", "completed_task", "query_result", "stashed_session"]
ResolutionStatus = Literal["none", "resolved", "ambiguous"]

DEFAULT_REFERENT_TTL_SECONDS = 900
STASHED_REFERENT_TTL_SECONDS = 1800
MAX_REFERENT_ITEMS = 20
_SENSITIVE_KEY_FRAGMENTS = ("pin", "otp", "password", "token", "secret", "auth", "callback")
_RECIPIENT_REFERENCE_RE = re.compile(
    r"\b(?:him|her|them|that\s+(?:person|recipient)|this\s+(?:person|recipient)|"
    r"that\s+(?:guy|babe|customer)|same\s+(?:person|recipient|guy|babe|customer)|"
    r"previous\s+(?:person|recipient|guy|babe|customer)|the\s+previous\s+one|"
    r"(?:send|transfer|pay)\s+am\b|(?:to|for)\s+am\b)\b",
    re.IGNORECASE,
)
_PHONE_REFERENCE_RE = re.compile(
    r"\b(?:that\s+(?:number|line)|this\s+(?:number|line)|same\s+(?:number|line)|"
    r"previous\s+(?:number|line)|that\s+sim|same\s+sim|"
    r"(?:buy|purchase|top\s*up)\s+(?:airtime|data)\s+(?:for\s+)?am\b)\b",
    re.IGNORECASE,
)
_AMOUNT_REFERENCE_RE = re.compile(
    r"\b(?:same\s+(?:amount|money|thing)|that\s+(?:amount|money)|this\s+(?:amount|money)|"
    r"previous\s+(?:amount|money)|same\s+again|do\s+(?:it\s+)?again|send\s+(?:it\s+)?again|"
    r"buy\s+(?:it\s+)?again|purchase\s+(?:it\s+)?again|repeat(?:\s+(?:it|that))?|again)\b",
    re.IGNORECASE,
)
_SOURCE_ACCOUNT_REFERENCE_RE = re.compile(
    r"\b(?:same\s+(?:account|bank|source|debit\s+account)|that\s+(?:account|bank|source|debit\s+account)|"
    r"this\s+(?:account|bank|source|debit\s+account)|previous\s+(?:account|bank|source|debit\s+account)|"
    r"(?:from|using|use|with|debit(?:ing)?|charge)\s+(?:the\s+)?same\s+(?:account|bank))\b",
    re.IGNORECASE,
)
_DATA_PLAN_REFERENCE_RE = re.compile(
    r"\b(?:that\s+(?:data\s+)?plan|this\s+(?:data\s+)?plan|same\s+(?:data\s+)?plan|"
    r"that\s+bundle|this\s+bundle|the\s+(?:first|second|third|monthly|weekly|daily)\s+one|"
    r"(?:option|number|#)\s*\d{1,2}|"
    r"(?:buy|get|purchase)\s+(?:it|that|that\s+one|(?:the\s+)?(?:monthly|weekly|daily)\s+one|"
    r"option\s+\d{1,2}|number\s+\d{1,2}|the\s+plan|the\s+bundle))\b",
    re.IGNORECASE,
)
_DATA_PLAN_OPTION_RE = re.compile(
    r"\b(?:option|number|#)\s*(\d{1,2})\b|\b(?:the\s+)?(first|second|third)\s+one\b",
    re.IGNORECASE,
)
_DATA_PLAN_VALIDITY_WORDS = {"daily": 1, "weekly": 7, "monthly": 30}
_EXPLICIT_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)\s*\d|"
    r"\b\d[\d,]*(?:\.\d+)?\s*[kKhH]\b|"
    r"\b(?:send|transfer|pay|remit|buy|purchase|top\s*up)\s+"
    r"(?:me\s+|him\s+|her\s+|them\s+|am\s+|airtime\s+|data\s+)?"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?(?:\s*[kKhH])?\b",
    re.IGNORECASE,
)


class ReferentMemoryItem(BaseModel):
    """One safe short-term referent derived from trusted structured state."""

    referent_type: ReferentType
    source: ReferentSource
    label: str | None = None
    entity_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at_ts: int = Field(default_factory=lambda: int(time.time()))
    ttl_seconds: int = DEFAULT_REFERENT_TTL_SECONDS


class ShortTermReferentMemory(BaseModel):
    """Bounded referent memory stored in orchestrator checkpoint state."""

    items: list[ReferentMemoryItem] = Field(default_factory=list)


class ReferentResolution(BaseModel):
    """Resolution result for one referential phrase class."""

    status: ResolutionStatus
    referent_type: ReferentType
    item: ReferentMemoryItem | None = None
    candidates: list[ReferentMemoryItem] = Field(default_factory=list)
    reason: str | None = None


def _memory_for_state(state: Any) -> ShortTermReferentMemory:
    memory = getattr(state, "referent_memory", None)
    if isinstance(memory, ShortTermReferentMemory):
        return memory
    restored = ShortTermReferentMemory.model_validate(memory) if isinstance(memory, dict) else ShortTermReferentMemory()
    state.referent_memory = restored
    return restored


def _safe_scalar(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int | float | bool):
        return value
    return None


def _safe_data(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for raw_key, value in values.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
            continue
        scalar = _safe_scalar(value)
        if scalar is not None:
            safe[key] = scalar
            continue
        if isinstance(value, list):
            safe_items = [_safe_scalar(item) for item in value[:10]]
            safe_list = [item for item in safe_items if item is not None]
            if safe_list:
                safe[key] = safe_list
        elif isinstance(value, dict):
            nested = _safe_data(value)
            if nested:
                safe[key] = nested
    return safe


def _first_text(*values: Any) -> str | None:
    for value in values:
        safe = _safe_scalar(value)
        if safe is not None:
            return str(safe)
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_active(item: ReferentMemoryItem, now: int) -> bool:
    return item.created_at_ts + item.ttl_seconds > now


def prune_referent_memory(state: Any, *, now: int | None = None) -> ShortTermReferentMemory:
    memory = _memory_for_state(state)
    current_time = int(time.time()) if now is None else now
    memory.items = [item for item in memory.items if _is_active(item, current_time)]
    return memory


def _dedupe_key(item: ReferentMemoryItem) -> tuple[str, str]:
    data = item.data
    key = (
        item.entity_id
        or _first_text(
            data.get("beneficiary_id"),
            data.get("id"),
            data.get("recipient_account"),
            data.get("account_number"),
            data.get("phone"),
            data.get("recipient_phone"),
            data.get("target_phone"),
            data.get("transaction_id"),
            data.get("reference"),
            data.get("plan_code"),
            data.get("item_code"),
            data.get("amount"),
            item.label,
        )
        or ""
    )
    return item.referent_type, key


def _resolution_dedupe_key(item: ReferentMemoryItem) -> str:
    return _dedupe_key(item)[1] or f"{item.referent_type}:{item.label or id(item)}"


def _stash_id_for_item(item: ReferentMemoryItem) -> str:
    return str(item.data.get("stash_id") or "").strip()


def _should_replace_memory_item(existing: ReferentMemoryItem, candidate: ReferentMemoryItem) -> bool:
    if _dedupe_key(existing) != _dedupe_key(candidate):
        return False
    if candidate.source == "stashed_session":
        return existing.source == "stashed_session" and _stash_id_for_item(existing) == _stash_id_for_item(candidate)
    if existing.source == "stashed_session":
        return False
    return True


def _prefer_resolution_item(existing: ReferentMemoryItem, candidate: ReferentMemoryItem) -> ReferentMemoryItem:
    if candidate.confidence != existing.confidence:
        return candidate if candidate.confidence > existing.confidence else existing
    if candidate.referent_type == "recipient" and existing.referent_type == "beneficiary":
        return candidate
    if candidate.created_at_ts > existing.created_at_ts:
        return candidate
    return existing


def _remember_item(state: Any, item: ReferentMemoryItem) -> None:
    if not item.label and not item.data:
        return
    memory = prune_referent_memory(state)
    memory.items = [existing for existing in memory.items if not _should_replace_memory_item(existing, item)]
    memory.items.append(item)
    if len(memory.items) > MAX_REFERENT_ITEMS:
        memory.items = memory.items[-MAX_REFERENT_ITEMS:]


def _frame_source(frame: ContextFrame, entity: ContextEntity) -> ReferentSource:
    if entity.focused_referent is not None or frame.frame_id.startswith("query_"):
        return "query_result"
    return "context_frame"


def _frame_confidence(frame: ContextFrame, entity: ContextEntity, index: int) -> float:
    if entity.focused_referent is not None:
        return 0.95
    if len(frame.items) == 1:
        return 0.92
    if frame.frame_type in {ContextFrameType.RECEIPT, ContextFrameType.TRANSACTION_DETAIL}:
        return 0.92
    if frame.focus_index == index and frame.frame_type != ContextFrameType.BENEFICIARY_LIST:
        return 0.88
    return 0.72


def _referent_data_from_focused(entity: ContextEntity) -> dict[str, Any] | None:
    referent = entity.focused_referent
    if referent is None:
        return None
    raw = referent.model_dump(exclude_none=True)
    return _safe_data(
        {
            "id": raw.get("entity_id") or entity.entity_id,
            "beneficiary_id": raw.get("entity_id") or entity.entity_id,
            "alias": raw.get("recipient_name") or raw.get("label") or entity.label,
            "account_name": raw.get("recipient_resolved_name") or raw.get("recipient_name"),
            "account_number": raw.get("recipient_account"),
            "bank_name": raw.get("recipient_bank_name"),
            "bank_code": raw.get("recipient_bank_code"),
            "recipient_name": raw.get("recipient_name") or raw.get("label") or entity.label,
            "recipient_resolved_name": raw.get("recipient_resolved_name"),
            "recipient_account": raw.get("recipient_account"),
            "recipient_bank_name": raw.get("recipient_bank_name"),
            "recipient_bank_code": raw.get("recipient_bank_code"),
            "beneficiary_type": "transfer",
        }
    )


def _beneficiary_data_from_entity(entity: ContextEntity) -> dict[str, Any]:
    data = entity.data
    account_number = _first_text(data.get("account_number"), data.get("recipient_account"), data.get("account"))
    bank_name = _first_text(data.get("bank_name"), data.get("recipient_bank_name"), data.get("bank"))
    bank_code = _first_text(data.get("bank_code"), data.get("recipient_bank_code"))
    alias = _first_text(data.get("alias"), data.get("recipient_name"), entity.label)
    account_name = _first_text(data.get("account_name"), data.get("recipient_resolved_name"), alias)
    return _safe_data(
        {
            "id": data.get("id") or data.get("beneficiary_id") or entity.entity_id,
            "beneficiary_id": data.get("beneficiary_id") or data.get("id") or entity.entity_id,
            "alias": alias,
            "account_name": account_name,
            "account_number": account_number,
            "bank_name": bank_name,
            "bank_code": bank_code,
            "recipient_name": alias,
            "recipient_resolved_name": account_name,
            "recipient_account": account_number,
            "recipient_bank_name": bank_name,
            "recipient_bank_code": bank_code,
            "beneficiary_type": data.get("beneficiary_type") or "transfer",
        }
    )


def _remember_recipient_like(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    entity_id: str | None,
    source: ReferentSource,
    confidence: float,
    created_at_ts: int,
    ttl_seconds: int,
    as_beneficiary: bool,
    extra_data: dict[str, Any] | None = None,
) -> None:
    if not (_first_text(data.get("recipient_account"), data.get("account_number")) or label):
        return
    referent_data = _safe_data({**data, **(extra_data or {})})
    if as_beneficiary:
        _remember_item(
            state,
            ReferentMemoryItem(
                referent_type="beneficiary",
                source=source,
                label=label,
                entity_id=entity_id,
                data=referent_data,
                confidence=confidence,
                created_at_ts=created_at_ts,
                ttl_seconds=ttl_seconds,
            ),
        )
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="recipient",
            source=source,
            label=label,
            entity_id=entity_id,
            data=referent_data,
            confidence=confidence,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def _remember_amount(
    state: Any,
    *,
    amount: Any,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    value = _first_number(amount)
    if value is None:
        return
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="amount",
            source=source,
            label=f"{value:g}",
            data=_safe_data({"amount": value, **(extra_data or {})}),
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def _remember_phone(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    phone = _first_text(
        data.get("phone"),
        data.get("recipient_phone"),
        data.get("target_phone"),
        data.get("phone_number"),
    )
    if not phone:
        return
    phone_data = _safe_data(
        {
            "phone": phone,
            "recipient_phone": phone,
            "target_phone": phone,
            "recipient_name": data.get("recipient_name"),
            "network": data.get("network"),
            **(extra_data or {}),
        }
    )
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="phone",
            source=source,
            label=label or phone,
            data=phone_data,
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def _remember_source_account(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    account_id = _first_text(data.get("source_account_id"), data.get("account_id"), data.get("id"))
    account_number = _first_text(data.get("source_account_number"), data.get("account_number"))
    bank_name = _first_text(data.get("source_bank_name"), data.get("bank_name"))
    if not (account_id or account_number or bank_name):
        return
    account_data = _safe_data(
        {
            "source_account_id": account_id,
            "account_id": account_id,
            "source_account_number": account_number,
            "account_number": account_number,
            "source_bank_name": bank_name,
            "bank_name": bank_name,
            "source_account_name": data.get("source_account_name") or data.get("account_name"),
            **(extra_data or {}),
        }
    )
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="source_account",
            source=source,
            label=label or bank_name or account_number,
            entity_id=account_id,
            data=account_data,
            confidence=0.85,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def _remember_transaction(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    entity_id: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    reference = _first_text(data.get("transaction_id"), data.get("reference"), data.get("task_id"), entity_id)
    if not reference and not label:
        return
    tx_data = _safe_data({**data, "transaction_id": reference, "reference": reference, **(extra_data or {})})
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="transaction",
            source=source,
            label=label,
            entity_id=reference,
            data=tx_data,
            confidence=0.88,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def _remember_data_plan(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
) -> None:
    plan_code = _first_text(data.get("plan_code"), data.get("item_code"))
    plan_name = _first_text(data.get("plan_name"), data.get("name"), label)
    if not plan_code and not plan_name:
        return
    plan_data = _safe_data(
        {
            "plan_code": plan_code,
            "item_code": plan_code,
            "plan_name": plan_name,
            "name": plan_name,
            "network": data.get("network"),
            "amount": data.get("amount"),
            "size_gb": data.get("size_gb"),
            "validity_days": data.get("validity_days"),
            "biller_code": data.get("biller_code"),
            "index": data.get("index"),
            "tags": data.get("tags"),
        }
    )
    _remember_item(
        state,
        ReferentMemoryItem(
            referent_type="data_plan",
            source=source,
            label=plan_name,
            entity_id=plan_code,
            data=plan_data,
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_referents_from_frame(state: Any, frame: ContextFrame) -> None:
    """Seed canonical referents from a trusted visible context frame."""
    prune_referent_memory(state)
    for index, entity in enumerate(frame.items):
        source = _frame_source(frame, entity)
        confidence = _frame_confidence(frame, entity, index)
        label = entity.label
        if entity.focused_referent is not None:
            focused_data = _referent_data_from_focused(entity)
            if focused_data:
                _remember_recipient_like(
                    state,
                    data=focused_data,
                    label=label,
                    entity_id=entity.entity_id,
                    source=source,
                    confidence=confidence,
                    created_at_ts=frame.created_at_ts,
                    ttl_seconds=frame.ttl_seconds,
                    as_beneficiary=True,
                )

        safe_entity_data = _safe_data(entity.data)
        if entity.entity_type == EntityType.BENEFICIARY:
            beneficiary_data = _beneficiary_data_from_entity(entity)
            _remember_recipient_like(
                state,
                data=beneficiary_data,
                label=label,
                entity_id=entity.entity_id,
                source=source,
                confidence=confidence,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
                as_beneficiary=True,
            )
        elif entity.entity_type == EntityType.ACCOUNT:
            _remember_source_account(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
        elif entity.entity_type == EntityType.DATA_PLAN:
            _remember_data_plan(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
        elif entity.entity_type == EntityType.TRANSACTION:
            _remember_transaction(
                state,
                data=safe_entity_data,
                label=label,
                entity_id=entity.entity_id,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            _remember_amount(
                state,
                amount=safe_entity_data.get("amount"),
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            _remember_phone(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            _remember_source_account(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            recipient_data = _beneficiary_data_from_entity(entity)
            if recipient_data.get("recipient_account") or recipient_data.get("recipient_name"):
                _remember_recipient_like(
                    state,
                    data=recipient_data,
                    label=_first_text(recipient_data.get("recipient_name"), label),
                    entity_id=entity.entity_id,
                    source=source,
                    confidence=confidence,
                    created_at_ts=frame.created_at_ts,
                    ttl_seconds=frame.ttl_seconds,
                    as_beneficiary=False,
                )


def remember_referents_from_completed_task(state: Any, task: Any) -> None:
    """Seed canonical referents from a completed task payload."""
    task_type = str(getattr(task, "type", "") or "").strip()
    payload = getattr(task, "payload", None)
    if not isinstance(payload, dict) or task_type not in {"transfer", "airtime", "data"}:
        return
    created_at_ts = int(time.time())
    safe_payload = _safe_data(payload)
    label = None
    if task_type == "transfer":
        label = _first_text(
            payload.get("recipient_resolved_name"),
            payload.get("recipient_name"),
            payload.get("recipient_account"),
        )
        recipient_data = _safe_data(
            {
                "id": payload.get("beneficiary_id"),
                "beneficiary_id": payload.get("beneficiary_id"),
                "alias": payload.get("recipient_name"),
                "account_name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                "account_number": payload.get("recipient_account"),
                "bank_name": payload.get("recipient_bank_name"),
                "bank_code": payload.get("recipient_bank_code"),
                "recipient_name": payload.get("recipient_name"),
                "recipient_resolved_name": payload.get("recipient_resolved_name"),
                "recipient_account": payload.get("recipient_account"),
                "recipient_bank_name": payload.get("recipient_bank_name"),
                "recipient_bank_code": payload.get("recipient_bank_code"),
                "beneficiary_type": "transfer",
            }
        )
        _remember_recipient_like(
            state,
            data=recipient_data,
            label=label,
            entity_id=_first_text(payload.get("beneficiary_id"), payload.get("transaction_id")),
            source="completed_task",
            confidence=0.94,
            created_at_ts=created_at_ts,
            ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
            as_beneficiary=bool(payload.get("beneficiary_id")),
        )
    else:
        phone = _first_text(payload.get("recipient_phone"), payload.get("target_phone"), payload.get("phone_number"))
        _remember_phone(
            state,
            data={**safe_payload, "phone": phone, "recipient_phone": phone, "target_phone": phone},
            label=phone,
            source="completed_task",
            created_at_ts=created_at_ts,
            ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
        )
        if task_type == "data":
            _remember_data_plan(
                state,
                data=safe_payload,
                label=_first_text(payload.get("plan_name"), payload.get("plan_code")),
                source="completed_task",
                created_at_ts=created_at_ts,
                ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
            )

    _remember_amount(
        state,
        amount=payload.get("amount"),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )
    _remember_source_account(
        state,
        data=safe_payload,
        label=_first_text(payload.get("source_bank_name"), payload.get("source_account_number")),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )
    _remember_transaction(
        state,
        data={**safe_payload, "task_type": task_type, "task_id": getattr(task, "id", None)},
        label=label or _first_text(payload.get("recipient_phone"), payload.get("target_phone"), task_type),
        entity_id=_first_text(payload.get("transaction_id"), payload.get("reference"), getattr(task, "id", None)),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )


def _task_type(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("type") or "").strip()
    return str(getattr(task, "type", "") or "").strip()


def _task_payload(task: Any) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task, dict) else getattr(task, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _task_id(task: Any, fallback: str) -> str:
    raw_id = task.get("id") if isinstance(task, dict) else getattr(task, "id", None)
    return str(raw_id or fallback)


def remember_referents_from_stashed_session(state: Any, session: dict[str, Any]) -> None:
    """Seed safe referents from a stashed transaction session."""
    stash_id = _first_text(session.get("stash_id"))
    tasks = session.get("tasks")
    if not stash_id or not isinstance(tasks, dict):
        return
    created_at_ts = int(session.get("stashed_at_ts") or time.time())
    extra_data = {"stash_id": stash_id}
    for fallback_id, task in tasks.items():
        task_type = _task_type(task)
        if task_type not in {"transfer", "airtime", "data"}:
            continue
        payload = _task_payload(task)
        if not payload:
            continue
        safe_payload = _safe_data(payload)
        task_identifier = _task_id(task, str(fallback_id))
        label = None
        if task_type == "transfer":
            label = _first_text(
                payload.get("recipient_resolved_name"),
                payload.get("recipient_name"),
                payload.get("recipient_account"),
            )
            recipient_data = _safe_data(
                {
                    "id": payload.get("beneficiary_id"),
                    "beneficiary_id": payload.get("beneficiary_id"),
                    "alias": payload.get("recipient_name"),
                    "account_name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                    "account_number": payload.get("recipient_account"),
                    "bank_name": payload.get("recipient_bank_name"),
                    "bank_code": payload.get("recipient_bank_code"),
                    "recipient_name": payload.get("recipient_name"),
                    "recipient_resolved_name": payload.get("recipient_resolved_name"),
                    "recipient_account": payload.get("recipient_account"),
                    "recipient_bank_name": payload.get("recipient_bank_name"),
                    "recipient_bank_code": payload.get("recipient_bank_code"),
                    "beneficiary_type": "transfer",
                }
            )
            _remember_recipient_like(
                state,
                data=recipient_data,
                label=label,
                entity_id=_first_text(payload.get("beneficiary_id"), payload.get("transaction_id"), task_identifier),
                source="stashed_session",
                confidence=0.93,
                created_at_ts=created_at_ts,
                ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
                as_beneficiary=bool(payload.get("beneficiary_id")),
                extra_data=extra_data,
            )
        else:
            phone = _first_text(
                payload.get("recipient_phone"),
                payload.get("target_phone"),
                payload.get("phone_number"),
            )
            _remember_phone(
                state,
                data={**safe_payload, "phone": phone, "recipient_phone": phone, "target_phone": phone},
                label=phone,
                source="stashed_session",
                created_at_ts=created_at_ts,
                ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
                extra_data=extra_data,
            )

        _remember_amount(
            state,
            amount=payload.get("amount"),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )
        _remember_source_account(
            state,
            data=safe_payload,
            label=_first_text(payload.get("source_bank_name"), payload.get("source_account_number")),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )
        _remember_transaction(
            state,
            data={**safe_payload, "task_type": task_type, "task_id": task_identifier},
            label=label or _first_text(payload.get("recipient_phone"), payload.get("target_phone"), task_type),
            entity_id=_first_text(payload.get("transaction_id"), payload.get("reference"), task_identifier),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )


def forget_stashed_referents(state: Any, stash_ids: set[str] | list[str] | tuple[str, ...]) -> ShortTermReferentMemory:
    """Remove stashed-session referents for specific stash ids."""
    memory = prune_referent_memory(state)
    ids = {str(stash_id) for stash_id in stash_ids if str(stash_id).strip()}
    if not ids:
        return memory
    memory.items = [
        item
        for item in memory.items
        if not (item.source == "stashed_session" and str(item.data.get("stash_id") or "") in ids)
    ]
    return memory


def _resolve_candidates(state: Any, referent_types: set[ReferentType]) -> list[ReferentMemoryItem]:
    memory = prune_referent_memory(state)
    candidates = [item for item in memory.items if item.referent_type in referent_types]
    deduped: dict[str, ReferentMemoryItem] = {}
    for item in candidates:
        key = _resolution_dedupe_key(item)
        existing = deduped.get(key)
        deduped[key] = item if existing is None else _prefer_resolution_item(existing, item)
    return sorted(
        deduped.values(),
        key=lambda item: (item.confidence, item.created_at_ts),
        reverse=True,
    )


def _resolve_reference(
    state: Any,
    *,
    text: str | None,
    referent_type: ReferentType,
    candidate_types: set[ReferentType],
    pattern: re.Pattern[str],
) -> ReferentResolution:
    if not pattern.search(text or ""):
        return ReferentResolution(status="none", referent_type=referent_type, reason="no_reference_phrase")
    candidates = _resolve_candidates(state, candidate_types)
    if not candidates:
        return ReferentResolution(status="none", referent_type=referent_type, reason="no_candidates")
    top = candidates[0]
    if len(candidates) == 1:
        if top.confidence < 0.75:
            return ReferentResolution(status="none", referent_type=referent_type, reason="low_confidence")
        return ReferentResolution(status="resolved", referent_type=referent_type, item=top)
    second = candidates[1]
    if top.confidence >= 0.7 and top.confidence - second.confidence < 0.15:
        return ReferentResolution(
            status="ambiguous",
            referent_type=referent_type,
            candidates=candidates[:5],
            reason="multiple_candidates",
        )
    if top.confidence < 0.75:
        return ReferentResolution(status="none", referent_type=referent_type, reason="low_confidence")
    return ReferentResolution(status="resolved", referent_type=referent_type, item=top)


def resolve_recipient_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="recipient",
        candidate_types={"beneficiary", "recipient"},
        pattern=_RECIPIENT_REFERENCE_RE,
    )


def resolve_phone_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="phone",
        candidate_types={"phone"},
        pattern=_PHONE_REFERENCE_RE,
    )


def resolve_amount_reference(state: Any, text: str | None) -> ReferentResolution:
    if _EXPLICIT_AMOUNT_RE.search(text or ""):
        return ReferentResolution(status="none", referent_type="amount", reason="explicit_amount_present")
    return _resolve_reference(
        state,
        text=text,
        referent_type="amount",
        candidate_types={"amount"},
        pattern=_AMOUNT_REFERENCE_RE,
    )


def resolve_source_account_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="source_account",
        candidate_types={"source_account"},
        pattern=_SOURCE_ACCOUNT_REFERENCE_RE,
    )


def resolve_data_plan_reference(state: Any, text: str | None) -> ReferentResolution:
    if not _DATA_PLAN_REFERENCE_RE.search(text or ""):
        return ReferentResolution(status="none", referent_type="data_plan", reason="no_reference_phrase")
    candidates = _resolve_candidates(state, {"data_plan"})
    if not candidates:
        return ReferentResolution(status="none", referent_type="data_plan", reason="no_candidates")

    option_match = _DATA_PLAN_OPTION_RE.search(text or "")
    if option_match:
        ordinal = {"first": 1, "second": 2, "third": 3}.get((option_match.group(2) or "").lower())
        selected_index = int(option_match.group(1) or ordinal or 0)
        if selected_index:
            for item in candidates:
                try:
                    item_index = int(item.data.get("index") or 0)
                except (TypeError, ValueError):
                    item_index = 0
                if item_index == selected_index:
                    return ReferentResolution(status="resolved", referent_type="data_plan", item=item)
            if len(candidates) >= selected_index:
                return ReferentResolution(
                    status="resolved",
                    referent_type="data_plan",
                    item=candidates[selected_index - 1],
                )

    normalized = (text or "").lower()
    for word, days in _DATA_PLAN_VALIDITY_WORDS.items():
        if word not in normalized:
            continue
        matches = [item for item in candidates if int(item.data.get("validity_days") or 0) == days]
        if len(matches) == 1:
            return ReferentResolution(status="resolved", referent_type="data_plan", item=matches[0])
        if len(matches) > 1:
            return ReferentResolution(
                status="ambiguous",
                referent_type="data_plan",
                candidates=matches[:5],
                reason="multiple_validity_matches",
            )

    return _resolve_reference(
        state,
        text=text,
        referent_type="data_plan",
        candidate_types={"data_plan"},
        pattern=_DATA_PLAN_REFERENCE_RE,
    )


def build_resolved_referents(state: Any, text: str | None) -> dict[str, Any]:
    """Resolve all known reference classes for the current text."""
    results = {
        "recipient": resolve_recipient_reference(state, text),
        "phone": resolve_phone_reference(state, text),
        "amount": resolve_amount_reference(state, text),
        "source_account": resolve_source_account_reference(state, text),
        "data_plan": resolve_data_plan_reference(state, text),
    }
    return {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in results.items()
        if value.status != "none"
    }


def build_referent_memory_summary(state: Any) -> str:
    memory = prune_referent_memory(state)
    if not memory.items:
        return ""
    lines = ["Referent Memory:"]
    for item in memory.items[-6:]:
        label = item.label or item.data.get("recipient_name") or item.data.get("phone") or item.data.get("amount")
        if label is None:
            continue
        lines.append(f"- {item.referent_type}: {label} ({item.source}, confidence={item.confidence:.2f})")
    return "\n".join(lines)


def referent_memory_ttl_seconds(state: dict[str, Any]) -> int:
    """Return remaining TTL for serialized referent memory in a graph state dict."""
    memory = state.get("referent_memory")
    if isinstance(memory, ShortTermReferentMemory):
        items = memory.items
    elif isinstance(memory, dict):
        try:
            items = ShortTermReferentMemory.model_validate(memory).items
        except Exception:
            return 0
    else:
        return 0

    now = int(time.time())
    return max([0, *[(item.created_at_ts + item.ttl_seconds) - now for item in items]])
