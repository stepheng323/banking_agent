"""Data-plan shortcut helpers for gate domain stages."""

import re
import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity
from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

_DATA_PLAN_QUERY_RE = re.compile(
    r"\b(?:how\s+much|price|cost|show|list|what\s+can\s+i\s+get|do\s+you\s+have|available)\b",
    re.IGNORECASE,
)
_DATA_PLAN_QUERY_SIGNAL_RE = re.compile(
    r"\b(?:data|bundle|mtn|glo|airtel|9mobile|\d+(?:\.\d+)?\s*(?:gb|g|mb|m))\b",
    re.IGNORECASE,
)
_DATA_PLAN_BUY_REFERENCE_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+|abeg\s+|jowo\s+|biko\s+)?(?:buy|get|purchase)\s+"
    r"(?:it|that|that\s+one|(?:the\s+)?(?:monthly|weekly|daily)\s+one|"
    r"option\s+\d{1,2}|number\s+\d{1,2}|the\s+plan|the\s+bundle)\b",
    re.IGNORECASE,
)
_DATA_PLAN_OPTION_REFERENCE_RE = re.compile(r"\b(?:option|number|#)\s*(\d{1,2})\b", re.IGNORECASE)
_DATA_PLAN_VALIDITY_REFERENCES = {"daily": 1, "weekly": 7, "monthly": 30}
_DATA_SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(gb|g|mb|m)\b", re.IGNORECASE)
_DATA_BUDGET_RE = re.compile(r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMhH]?)")


def _is_data_plan_query_request(message_text: str) -> bool:
    normalized = " ".join(message_text.strip().split())
    return bool(normalized and _DATA_PLAN_QUERY_RE.search(normalized) and _DATA_PLAN_QUERY_SIGNAL_RE.search(normalized))


def _is_data_plan_reference_purchase_request(message_text: str) -> bool:
    return bool(_DATA_PLAN_BUY_REFERENCE_RE.search(message_text))


def _parse_data_query_amount(text: str) -> float | None:
    marker_match = re.search(
        r"\b(?:for|within|under|with)\s+(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMhH]?)",
        text,
        re.IGNORECASE,
    )
    if marker_match:
        amount = float(marker_match.group(1).replace(",", ""))
        suffix = (marker_match.group(2) or "").lower()
        if suffix == "k":
            amount *= 1000
        elif suffix == "h":
            amount *= 100
        elif suffix == "m":
            amount *= 1_000_000
        return amount if amount > 0 else None

    for match in _DATA_BUDGET_RE.finditer(text):
        if re.match(r"\s*(?:gb|g|mb|m)\b", text[match.end() :], re.IGNORECASE):
            continue
        amount = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            amount *= 1000
        elif suffix == "h":
            amount *= 100
        elif suffix == "m":
            amount *= 1_000_000
        if amount > 0:
            return amount
    return None


def _extract_data_query_payload(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "data_plan_query",
        "message": text,
        "instruction": text,
        "skip_finalize_summary": True,
    }
    for token in re.findall(r"[A-Za-z0-9]+", text):
        network = normalize_network_name(token)
        if network:
            payload["network"] = network
            break
    if size_match := _DATA_SIZE_RE.search(text):
        payload["size_preference"] = f"{size_match.group(1)}{size_match.group(2).upper()}"
        payload["plan_name"] = payload["size_preference"]
    if re.search(r"\b(?:monthly|month|30\s*days?)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "monthly"
    elif re.search(r"\b(?:weekly|week|7\s*days?)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "weekly"
    elif re.search(r"\b(?:daily|day|1\s*day)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "daily"
    if re.search(r"\b(?:best|most|maximum|max)\b", text, re.IGNORECASE):
        payload["selection_preference"] = "most_data"
    if re.search(r"\b(?:video|stream|youtube|netflix)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "video"
    elif re.search(r"\b(?:social|whatsapp|facebook|instagram|tiktok)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "social"
    elif re.search(r"\b(?:night|midnight)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "night"
    elif re.search(r"\bweekend\b", text, re.IGNORECASE):
        payload["usage_intent"] = "weekend"
    if re.search(r"\b(?:for|within|under|with)\s+(?:₦|ngn)?\s*\d", text, re.IGNORECASE):
        amount = _parse_data_query_amount(text)
        if amount is not None:
            payload["amount"] = amount
    return payload


def _extract_data_purchase_hints(text: str, *, phone_number: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for token in re.findall(r"[A-Za-z0-9]+", text):
        network = normalize_network_name(token)
        if network:
            payload["network"] = network
            break
    if size_match := _DATA_SIZE_RE.search(text):
        payload["size_preference"] = f"{size_match.group(1)}{size_match.group(2).upper()}"
        payload["plan_name"] = payload["size_preference"]
    if re.search(r"\b(?:monthly|month|30\s*days?)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "monthly"
    elif re.search(r"\b(?:weekly|week|7\s*days?)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "weekly"
    elif re.search(r"\b(?:daily|day|1\s*day)\b", text, re.IGNORECASE):
        payload["validity_preference"] = "daily"
    if re.search(r"\b(?:best|most|maximum|max)\b", text, re.IGNORECASE):
        payload["selection_preference"] = "most_data"
    elif re.search(r"\bcheapest\b", text, re.IGNORECASE):
        payload["selection_preference"] = "cheapest"
    if re.search(r"\b(?:video|stream|youtube|netflix)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "video"
    elif re.search(r"\b(?:social|whatsapp|facebook|instagram|tiktok)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "social"
    elif re.search(r"\b(?:night|midnight)\b", text, re.IGNORECASE):
        payload["usage_intent"] = "night"
    elif re.search(r"\bweekend\b", text, re.IGNORECASE):
        payload["usage_intent"] = "weekend"
    if re.search(r"\b(?:for|within|under|with)\s+(?:₦|ngn)?\s*\d", text, re.IGNORECASE):
        amount = _parse_data_query_amount(text)
        if amount is not None:
            payload["amount"] = amount
    _apply_self_data_target(payload, text=text, phone_number=phone_number)
    return payload


def _apply_self_data_target(payload: dict[str, Any], *, text: str, phone_number: str) -> None:
    if re.search(
        r"\b(?:buy|get|send)\s+me\b|\b(?:for\s+)?(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)\b",
        text,
        re.IGNORECASE,
    ):
        payload["target_phone"] = normalize_nigerian_phone(phone_number) or phone_number
        payload["is_self"] = True


def _is_data_plan_entity(entity: ContextEntity) -> bool:
    return entity.entity_type.value == "data_plan"


def _data_plan_display_key(entity: ContextEntity) -> tuple[str, str, float | None, int | None]:
    data = entity.data if isinstance(entity.data, dict) else {}
    try:
        raw_amount = data.get("amount")
        amount = float(raw_amount) if raw_amount is not None else None
    except (TypeError, ValueError):
        amount = None
    try:
        raw_validity = data.get("validity_days")
        validity = int(float(raw_validity)) if raw_validity is not None else None
    except (TypeError, ValueError):
        validity = None
    name = str(data.get("plan_name") or data.get("name") or entity.label or "").strip().casefold()
    name = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:gb|g)\b", r"\1gb", name)
    name = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:mb|m)\b", r"\1mb", name)
    name = re.sub(r"\s+", " ", name).strip()
    network = str(data.get("network") or "").strip().upper()
    return network, name, amount, validity


def _active_data_plan_entities_from_frames(ctx: GateContext) -> list[ContextEntity]:
    now = int(time.time())
    for frame in reversed(ctx.state_view.context_frames):
        if not frame.items or (frame.created_at_ts + frame.ttl_seconds) <= now:
            continue
        entities = [entity for entity in frame.items if _is_data_plan_entity(entity)]
        if not entities:
            continue
        unique_entities: list[ContextEntity] = []
        seen: set[tuple[str, str, float | None, int | None]] = set()
        for entity in entities:
            key = _data_plan_display_key(entity)
            if key in seen:
                continue
            seen.add(key)
            unique_entities.append(entity)
        return unique_entities
    return []


def _data_plan_context_data_for_reference(ctx: GateContext) -> dict[str, Any] | None:
    entities = _active_data_plan_entities_from_frames(ctx)
    if not entities:
        return None

    option_match = _DATA_PLAN_OPTION_REFERENCE_RE.search(ctx.message_text)
    if option_match:
        selected_index = int(option_match.group(1))
        for entity in entities:
            data = entity.data if isinstance(entity.data, dict) else {}
            try:
                item_index = int(data.get("index") or 0)
            except (TypeError, ValueError):
                item_index = 0
            if item_index == selected_index:
                return data
        if 0 < selected_index <= len(entities):
            data = entities[selected_index - 1].data
            return data if isinstance(data, dict) else None
        return None

    normalized = ctx.message_text.lower()
    for word, days in _DATA_PLAN_VALIDITY_REFERENCES.items():
        if word not in normalized:
            continue
        matches = []
        for entity in entities:
            data = entity.data if isinstance(entity.data, dict) else {}
            try:
                validity_days = int(data.get("validity_days") or 0)
            except (TypeError, ValueError):
                validity_days = 0
            if validity_days == days:
                matches.append(entity)
        if len(matches) == 1:
            data = matches[0].data
            return data if isinstance(data, dict) else None
        return None

    if len(entities) == 1:
        data = entities[0].data
        return data if isinstance(data, dict) else None
    return None


def _resolved_data_plan_payload(ctx: GateContext) -> dict[str, Any] | None:
    resolved = build_resolved_referents(ctx.state, ctx.message_text).get("data_plan")
    if isinstance(resolved, dict) and resolved.get("status") == "resolved":
        item = resolved.get("item")
        data = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else None
        if data:
            return data
    return _data_plan_context_data_for_reference(ctx)


__all__ = [
    "_apply_self_data_target",
    "_extract_data_purchase_hints",
    "_extract_data_query_payload",
    "_is_data_plan_query_request",
    "_is_data_plan_reference_purchase_request",
    "_resolved_data_plan_payload",
]
