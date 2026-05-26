import json
import re
import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity
from apps.chat.src.agent.orchestrator.context.referent_memory import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import (
    TaskSpec,
    TaskStage,
)
from apps.chat.src.agent.orchestrator.nodes.cancellation import build_cancellation_reset_updates
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
    _has_explicit_cancel,
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _next_direct_account_task_id,
    _next_direct_beneficiary_task_id,
    _resolve_beneficiary_suggestion_reply,
    _route_observability_updates,
)
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)
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
    if re.search(
        r"\b(?:buy|get|send)\s+me\b|\b(?:for\s+)?(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)\b",
        text,
        re.IGNORECASE,
    ):
        payload["target_phone"] = normalize_nigerian_phone(phone_number) or phone_number
        payload["is_self"] = True
    return payload


def _is_data_plan_entity(entity: ContextEntity) -> bool:
    return entity.entity_type.value == "data_plan"


def _data_plan_display_key(entity: ContextEntity) -> tuple[str, str, float | None, int | None]:
    data = entity.data if isinstance(entity.data, dict) else {}
    try:
        amount = float(data.get("amount")) if data.get("amount") is not None else None
    except (TypeError, ValueError):
        amount = None
    try:
        validity = int(float(data.get("validity_days"))) if data.get("validity_days") is not None else None
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
    for frame in reversed(ctx.state.context_frames):
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


async def _stage_beneficiary_suggestion(ctx: GateContext) -> dict[str, Any] | None:
    """Beneficiary save/dismiss from Redis suggestion."""
    if ctx.live_pending_interrupt or not ctx.redis_client:
        return None

    suggestion_key = f"user:{ctx.state.phone_number}:beneficiary_suggestion"
    try:
        suggestion_data = await ctx.redis_client.get(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_lookup_failed", error=str(exc))
        suggestion_data = None

    if not suggestion_data:
        return None

    suggestion_payload: dict[str, Any] | None
    try:
        parsed_payload = json.loads(suggestion_data)
        suggestion_payload = parsed_payload if isinstance(parsed_payload, dict) else None
    except Exception:
        suggestion_payload = None

    decision = _resolve_beneficiary_suggestion_reply(
        ctx.message_text,
        locale=ctx.current_locale,
        suggestion_payload=suggestion_payload,
    )
    logger.info(
        "beneficiary_suggestion_gate_decision",
        decision=decision.action,
        reason=decision.reason,
        locale=ctx.current_locale,
        alias_present=bool(decision.alias),
    )
    if decision.action in {"save_default", "save_alias"}:
        task_id = _next_direct_beneficiary_task_id(ctx.state.tasks)
        task_payload: dict[str, Any] = {
            "action": "save_beneficiary",
            "instruction": ctx.state.last_message_text,
            "message": ctx.state.last_message_text,
        }
        if decision.alias:
            task_payload["alias"] = decision.alias
        spec = TaskSpec(
            id=task_id,
            type="beneficiary",
            stage=TaskStage.DRAFT,
            payload=task_payload,
        )
        return {
            **ctx.gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            **_route_observability_updates(
                owner="guardrail",
                decision="beneficiary_save",
                target_domain="beneficiary",
                mode="new",
                route_source="beneficiary_suggestion",
                heuristic_type="guardrail_shortcut",
                heuristic_name="beneficiary_suggestion_reply",
            ),
        }

    try:
        await ctx.redis_client.delete(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
    else:
        logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)
    return None


async def _stage_balance_direct(ctx: GateContext) -> dict[str, Any] | None:
    """Direct balance check shortcut."""
    if ctx.live_pending_interrupt or not ctx.phrase_heavy_fastpath_allowed:
        return None
    if not _is_account_balance_request(ctx.message_text):
        return None
    cleanup_updates: dict[str, Any] = {}
    if _has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id = _next_direct_account_task_id(ctx.state.tasks)
    spec = TaskSpec(
        id=task_id,
        type="account",
        stage=TaskStage.DRAFT,
        payload={
            "action": "check_balance",
            "message": ctx.state.last_message_text,
            "instruction": ctx.state.last_message_text,
        },
    )
    logger.info("gate_direct_account_balance", task_id=task_id, with_cleanup=bool(cleanup_updates))
    return {
        **ctx.gate_updates,
        **cleanup_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "balance_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="balance_direct",
            target_domain="account",
            mode="new",
            route_source="account_balance_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="balance_request",
        ),
    }


async def _stage_account_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic account domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_account_domain_request(ctx.message_text)
    ):
        return None
    cleanup_updates: dict[str, Any] = {}
    if _has_explicit_cancel(ctx.message_text):
        cleanup_updates = await build_cancellation_reset_updates(ctx.state, ctx.redis_client)
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="account", mode="new")
    logger.info("gate_deterministic_account_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        **cleanup_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_account_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_account_domain",
            target_domain="account",
            mode="new",
            route_source="account_domain_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="account_domain_request",
        ),
    }


async def _stage_beneficiary_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic beneficiary domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_beneficiary_domain_request(ctx.message_text)
    ):
        return None
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="beneficiary", mode="new")
    logger.info("gate_deterministic_beneficiary_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_beneficiary_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_beneficiary_domain",
            target_domain="beneficiary",
            mode="new",
            route_source="beneficiary_domain_guard",
            heuristic_type="guardrail_shortcut",
            heuristic_name="beneficiary_list_request",
        ),
    }


async def _stage_airtime_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic airtime domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_airtime_request(ctx.message_text)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "airtime"):
        logger.info("gate_deterministic_airtime_domain_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_airtime_domain_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="airtime",
                mode="new",
                route_source="airtime_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name="obvious_airtime_request",
            ),
        }
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="airtime", mode="new")
    logger.info("gate_deterministic_airtime_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_airtime_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_airtime_domain",
            target_domain="airtime",
            mode="new",
            route_source="airtime_domain_guard",
            heuristic_type="slot_parser",
            heuristic_name="obvious_airtime_request",
        ),
    }


async def _stage_data_plan_query(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic catalog-question shortcut for data plan prices/availability."""
    normalized = " ".join(ctx.message_text.strip().split())
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not normalized
        or not _DATA_PLAN_QUERY_RE.search(normalized)
        or not _DATA_PLAN_QUERY_SIGNAL_RE.search(normalized)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "data"):
        logger.info("gate_data_plan_query_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_data_plan_query_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_plan_query_guard",
                heuristic_type="slot_parser",
                heuristic_name="data_plan_query",
            ),
        }

    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="data", mode="new")
    spec.payload.clear()
    spec.payload.update(_extract_data_query_payload(ctx.message_text))
    logger.info("gate_deterministic_data_plan_query", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_data_plan_query",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_data_plan_query",
            target_domain="data",
            mode="new",
            route_source="data_plan_query_guard",
            heuristic_type="slot_parser",
            heuristic_name="data_plan_query",
        ),
    }


async def _stage_data_plan_reference_purchase(ctx: GateContext) -> dict[str, Any] | None:
    """Route "buy it" after a data-plan answer into a normal data purchase."""
    if ctx.live_pending_interrupt or ctx.state.has_quote or not _DATA_PLAN_BUY_REFERENCE_RE.search(ctx.message_text):
        return None
    data = _resolved_data_plan_payload(ctx)
    if not data:
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "data"):
        logger.info("gate_data_plan_reference_purchase_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "data_plan_reference_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_plan_referent",
                heuristic_type="referent_memory",
                heuristic_name="data_plan_reference",
            ),
        }

    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="data", mode="new")
    spec.payload.update(
        {
            "action": "buy_data",
            "plan_code": data.get("plan_code") or data.get("item_code"),
            "plan_name": data.get("plan_name") or data.get("name"),
            "biller_code": data.get("biller_code"),
            "plan_size_gb": data.get("size_gb"),
            "plan_validity_days": data.get("validity_days"),
            "plan_tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
            "network": data.get("network"),
            "amount": data.get("amount"),
            "skip_extraction": True,
        }
    )
    if re.search(
        r"\b(?:for\s+)?(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)\b",
        ctx.message_text,
        re.IGNORECASE,
    ):
        spec.payload["target_phone"] = normalize_nigerian_phone(ctx.state.phone_number) or ctx.state.phone_number
        spec.payload["is_self"] = True
    logger.info("gate_data_plan_reference_purchase", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "data_plan_reference_purchase",
        **_route_observability_updates(
            owner="guardrail",
            decision="data_plan_reference_purchase",
            target_domain="data",
            mode="new",
            route_source="data_plan_referent",
            heuristic_type="referent_memory",
            heuristic_name="data_plan_reference",
        ),
    }


async def _stage_data_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic data domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_data_request(ctx.message_text)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state, "data"):
        logger.info("gate_deterministic_data_domain_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_data_domain_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name="obvious_data_request",
            ),
        }
    task_id, spec = _build_direct_domain_task(state=ctx.state, domain="data", mode="new")
    spec.payload.update(_extract_data_purchase_hints(ctx.message_text, phone_number=ctx.state.phone_number))
    logger.info("gate_deterministic_data_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_data_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_data_domain",
            target_domain="data",
            mode="new",
            route_source="data_domain_guard",
            heuristic_type="slot_parser",
            heuristic_name="obvious_data_request",
        ),
    }
