import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_query_domain_request,
    _is_structural_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from shared.services.unsupported_capability_detection import (
    detect_unsupported_capability,
    is_same_unsupported_capability_followup,
    normalize_unsupported_text,
    should_try_semantic_unsupported_capability,
)
from shared.services.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapability,
)
from shared.services.unsupported_capability_semantic import (
    validate_semantic_unsupported_capability,
    validate_unsupported_boundary_turn,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT = "unsupported_capability_followup"
FOLLOWUP_CONVERSATIONAL_LIMIT = 2

_SUPPORTED_BANKING_RE = re.compile(
    r"\b(?:"
    r"send|transfer|pay|buy|recharge|airtime|data|balance|balances|transaction|transactions|"
    r"history|statement|receipt|beneficiar\w*|account|accounts|schedule|scheduled|recurring"
    r")\b",
    re.IGNORECASE,
)
_GENERIC_CONTINUATION_RE = re.compile(
    r"\b(?:small|little|tiny|just|only|even|please|pls|abeg|jowo|biko|still|again|amount|money|cash)\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(?:₦|ngn|naira)?\s*\d[\d,]*(?:\.\d+)?\s*[km]?\b", re.IGNORECASE)


def normalize_boundary_text(text: str | None) -> str:
    return normalize_unsupported_text(text)


def is_live_boundary(boundary: CapabilityBoundary, *, now: float) -> bool:
    return now <= boundary.last_updated_ts + boundary.ttl_seconds


def coerce_boundary(raw: Any) -> CapabilityBoundary | None:
    if isinstance(raw, CapabilityBoundary):
        return raw
    if isinstance(raw, dict):
        try:
            return CapabilityBoundary.model_validate(raw)
        except Exception:
            return None
    return None


def recent_unsupported_boundary(ctx: GateContext) -> CapabilityBoundary | None:
    loaded_context = ctx.state.loaded_context if isinstance(ctx.state.loaded_context, dict) else {}
    grounding = loaded_context.get("conversation_grounding")
    if isinstance(grounding, dict):
        last_topic = str(grounding.get("last_topic") or "").strip()
        last_assistant = str(grounding.get("last_assistant_message") or "").strip()
        if last_topic == "unsupported_boundary" and last_assistant:
            capability = detect_unsupported_capability(last_assistant)
            if capability is not None:
                return CapabilityBoundary(key=capability.key, label=capability.label)

    history = loaded_context.get("history")
    if isinstance(history, list):
        for turn in reversed(history):
            if not isinstance(turn, dict):
                continue
            if str(turn.get("role") or "").strip().casefold() != "assistant":
                continue
            topic = str(turn.get("topic") or "").strip()
            metadata = turn.get("metadata")
            if not topic and isinstance(metadata, dict):
                topic = str(metadata.get("topic") or "").strip()
            if topic != "unsupported_boundary":
                continue
            capability = detect_unsupported_capability(str(turn.get("content") or ""))
            if capability is not None:
                return CapabilityBoundary(key=capability.key, label=capability.label)
            return None
    return None


def is_supported_banking_request(text: str) -> bool:
    if is_explicit_supported_banking_request(text):
        return True
    return bool(_SUPPORTED_BANKING_RE.search(normalize_boundary_text(text)))


def is_explicit_supported_banking_request(text: str) -> bool:
    if _classify_obvious_transfer_request(text) is not None:
        return True
    if _is_obvious_airtime_request(text) or _is_obvious_data_request(text):
        return True
    normalized = normalize_boundary_text(text)
    if _is_account_balance_request(normalized):
        return True
    if _is_structural_query_domain_request(normalized) or _is_query_domain_request(normalized):
        return True
    return False


def looks_like_boundary_followup(text: str, capability: UnsupportedCapability) -> bool:
    normalized = normalize_boundary_text(text)
    if not normalized:
        return False
    if is_same_unsupported_capability_followup(normalized, capability):
        return True
    if re.search(r"\b(?:small|little|tiny)\s+(?:amount|money|cash)\b", normalized):
        return True
    if _AMOUNT_RE.search(normalized) and _GENERIC_CONTINUATION_RE.search(normalized):
        return True
    if _GENERIC_CONTINUATION_RE.search(normalized) and len(normalized.split()) <= 7:
        return True
    return False


async def semantic_unsupported_capability(
    ctx: GateContext,
    text: str,
    *,
    allow_mixed: bool = False,
) -> UnsupportedCapability | None:
    if ctx.task_planner is None or not should_try_semantic_unsupported_capability(text):
        return None
    try:
        decision = await ctx.task_planner.classify_unsupported_capability(
            text,
            locale=ctx.current_locale,
            context="None",
            path_label="direct_path",
        )
    except Exception:
        logger.warning("semantic_unsupported_capability_failed")
        return None
    return validate_semantic_unsupported_capability(decision, allow_mixed=allow_mixed)


async def semantic_boundary_turn(
    ctx: GateContext,
    *,
    boundary: CapabilityBoundary,
    capability: UnsupportedCapability,
) -> UnsupportedBoundaryTurnOutput | None:
    if ctx.task_planner is None:
        return None
    try:
        decision = await ctx.task_planner.classify_unsupported_boundary_turn(
            ctx.message_text,
            boundary_key=capability.key,
            boundary_label=capability.label,
            followup_count=boundary.followup_count,
            locale=ctx.current_locale,
            context="None",
            path_label="direct_path",
        )
    except Exception:
        logger.warning("semantic_unsupported_boundary_turn_failed")
        return None
    return validate_unsupported_boundary_turn(decision, boundary_key=capability.key)


def boundary_update(boundary: CapabilityBoundary, *, followup_count: int, now: float) -> CapabilityBoundary:
    return CapabilityBoundary(
        key=boundary.key,
        label=boundary.label,
        followup_count=followup_count,
        created_at_ts=boundary.created_at_ts,
        last_updated_ts=now,
        ttl_seconds=boundary.ttl_seconds,
    )
