"""Storage helpers for short-term referent memory."""

from __future__ import annotations

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.models import (
    MAX_REFERENT_ITEMS,
    ReferentMemoryItem,
    ShortTermReferentMemory,
)
from apps.chat.src.agent.orchestrator.context.referents.values import first_text


def memory_for_state(state: Any) -> ShortTermReferentMemory:
    memory = getattr(state, "referent_memory", None)
    if isinstance(memory, ShortTermReferentMemory):
        return memory
    restored = ShortTermReferentMemory.model_validate(memory) if isinstance(memory, dict) else ShortTermReferentMemory()
    state.referent_memory = restored
    return restored


def _is_active(item: ReferentMemoryItem, now: int) -> bool:
    return item.created_at_ts + item.ttl_seconds > now


def prune_referent_memory(state: Any, *, now: int | None = None) -> ShortTermReferentMemory:
    memory = memory_for_state(state)
    current_time = int(time.time()) if now is None else now
    memory.items = [item for item in memory.items if _is_active(item, current_time)]
    return memory


def _dedupe_key(item: ReferentMemoryItem) -> tuple[str, str]:
    data = item.data
    key = (
        item.entity_id
        or first_text(
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


def remember_referent_item(state: Any, item: ReferentMemoryItem) -> None:
    if not item.label and not item.data:
        return
    memory = prune_referent_memory(state)
    memory.items = [existing for existing in memory.items if not _should_replace_memory_item(existing, item)]
    memory.items.append(item)
    if len(memory.items) > MAX_REFERENT_ITEMS:
        memory.items = memory.items[-MAX_REFERENT_ITEMS:]


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
