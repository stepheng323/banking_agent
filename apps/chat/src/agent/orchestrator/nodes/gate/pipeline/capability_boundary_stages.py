import re
from time import time
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _classify_obvious_transfer_request,
    _is_account_balance_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _is_query_domain_request,
    _is_structural_query_domain_request,
    _route_observability_updates,
)
from shared.i18n.renderer import render_message
from shared.services.unsupported_capabilities import (
    UNSUPPORTED_CAPABILITY_REGISTRY,
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapability,
    detect_unsupported_capability,
    get_unsupported_capability,
    is_same_unsupported_capability_followup,
    normalize_unsupported_text,
    should_try_semantic_unsupported_capability,
    unsupported_capability_params,
    validate_semantic_unsupported_capability,
    validate_unsupported_boundary_turn,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT = "unsupported_capability_followup"
_BOUNDARY_TTL_SECONDS = 600
_FOLLOWUP_CONVERSATIONAL_LIMIT = 2
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
_BOUNDARY_LOCALES = ("en", "pcm", "yo", "ha", "ig")

def _normalize(text: str | None) -> str:
    return normalize_unsupported_text(text)


def _is_live_boundary(boundary: CapabilityBoundary, *, now: float) -> bool:
    return now <= boundary.last_updated_ts + boundary.ttl_seconds


def _coerce_boundary(raw: Any) -> CapabilityBoundary | None:
    if isinstance(raw, CapabilityBoundary):
        return raw
    if isinstance(raw, dict):
        try:
            return CapabilityBoundary.model_validate(raw)
        except Exception:
            return None
    return None


def _assistant_history_texts(ctx: GateContext) -> list[str]:
    texts: list[str] = []
    if ctx.state.final_response:
        texts.append(str(ctx.state.final_response))
    loaded_context = ctx.state.loaded_context if isinstance(ctx.state.loaded_context, dict) else {}
    history = loaded_context.get("history")
    if isinstance(history, list):
        for turn in reversed(history[-6:]):
            if not isinstance(turn, dict):
                continue
            if str(turn.get("role", "")).strip().lower() != "assistant":
                continue
            content = str(turn.get("content") or "").strip()
            if content:
                texts.append(content)
    return texts


def _latest_reply_was_unsupported_boundary(ctx: GateContext) -> UnsupportedCapability | None:
    assistant_texts = [_normalize(text) for text in _assistant_history_texts(ctx)]
    if not assistant_texts:
        return None
    latest = assistant_texts[0]
    for capability in UNSUPPORTED_CAPABILITY_REGISTRY:
        expected = {
            _normalize(
                render_message(
                    "capability.unsupported_unavailable",
                    locale,
                    unsupported_capability_params(capability, locale=locale),
                )
            )
            for locale in _BOUNDARY_LOCALES
        }
        if latest in expected:
            return capability
    return None


def _is_supported_banking_request(text: str) -> bool:
    if _is_explicit_supported_banking_request(text):
        return True
    return bool(_SUPPORTED_BANKING_RE.search(_normalize(text)))


def _is_explicit_supported_banking_request(text: str) -> bool:
    if _classify_obvious_transfer_request(text) is not None:
        return True
    if _is_obvious_airtime_request(text) or _is_obvious_data_request(text):
        return True
    normalized = _normalize(text)
    if _is_account_balance_request(normalized):
        return True
    if _is_structural_query_domain_request(normalized) or _is_query_domain_request(normalized):
        return True
    return False


def _looks_like_boundary_followup(text: str, capability: UnsupportedCapability) -> bool:
    normalized = _normalize(text)
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


async def _semantic_unsupported_capability(
    ctx: GateContext,
    text: str,
    *,
    allow_mixed: bool = False,
) -> UnsupportedCapability | None:
    classifier = getattr(ctx.task_planner, "classify_unsupported_capability", None)
    if not callable(classifier) or not should_try_semantic_unsupported_capability(text):
        return None
    try:
        decision = await classifier(
            ctx.state.phone_number,
            text,
            locale=ctx.current_locale,
            context="None",
            path_label="direct_path",
        )
    except Exception:
        logger.warning("semantic_unsupported_capability_failed")
        return None
    return validate_semantic_unsupported_capability(decision, allow_mixed=allow_mixed)


async def _semantic_boundary_turn(
    ctx: GateContext,
    *,
    boundary: CapabilityBoundary,
    capability: UnsupportedCapability,
) -> UnsupportedBoundaryTurnOutput | None:
    classifier = getattr(ctx.task_planner, "classify_unsupported_boundary_turn", None)
    if not callable(classifier):
        return None
    try:
        decision = await classifier(
            ctx.state.phone_number,
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


def _boundary_update(boundary: CapabilityBoundary, *, followup_count: int, now: float) -> CapabilityBoundary:
    return CapabilityBoundary(
        key=boundary.key,
        label=boundary.label,
        followup_count=followup_count,
        created_at_ts=boundary.created_at_ts,
        last_updated_ts=now,
        ttl_seconds=boundary.ttl_seconds,
    )


async def _stage_capability_boundary_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Handle short follow-ups after unsupported capability refusals before stale context reuse."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
    ):
        return None

    now = time()
    boundary = _coerce_boundary(ctx.state.capability_boundary)
    if boundary is not None and not _is_live_boundary(boundary, now=now):
        ctx.gate_updates["capability_boundary"] = None
        return None

    capability = get_unsupported_capability(boundary.key) if boundary is not None else None
    if boundary is None:
        capability = _latest_reply_was_unsupported_boundary(ctx)
        if capability is not None:
            boundary = CapabilityBoundary(
                key=capability.key,
                label=capability.label,
                created_at_ts=now,
                last_updated_ts=now,
                ttl_seconds=_BOUNDARY_TTL_SECONDS,
            )

    if boundary is None or capability is None:
        return None

    detected_capability = detect_unsupported_capability(ctx.message_text)
    semantic_capability: UnsupportedCapability | None = None
    looks_like_followup = _looks_like_boundary_followup(ctx.message_text, capability)
    if detected_capability is not None and detected_capability.key != capability.key:
        ctx.gate_updates["capability_boundary"] = None
        return None

    if detected_capability is None and _is_explicit_supported_banking_request(ctx.message_text):
        ctx.gate_updates["capability_boundary"] = None
        return None

    boundary_decision: UnsupportedBoundaryTurnOutput | None = None
    if detected_capability is None:
        boundary_decision = await _semantic_boundary_turn(ctx, boundary=boundary, capability=capability)
        if boundary_decision is not None:
            if boundary_decision.action == "same_unsupported":
                looks_like_followup = True
            elif boundary_decision.action in {"new_unsupported", "supported_banking", "unrelated"}:
                ctx.gate_updates["capability_boundary"] = None
                return None

    if (
        detected_capability is None
        and boundary_decision is None
        and not looks_like_followup
        and _is_supported_banking_request(ctx.message_text)
    ):
        ctx.gate_updates["capability_boundary"] = None
        return None

    if detected_capability is None:
        semantic_capability = await _semantic_unsupported_capability(ctx, ctx.message_text)
        if semantic_capability is not None and semantic_capability.key != capability.key:
            ctx.gate_updates["capability_boundary"] = None
            return None

    if semantic_capability is None and not looks_like_followup:
        ctx.gate_updates["capability_boundary"] = None
        return None

    next_count = boundary.followup_count + 1
    updated_boundary = _boundary_update(boundary, followup_count=next_count, now=now)
    params = unsupported_capability_params(capability, locale=ctx.current_locale)
    if next_count > _FOLLOWUP_CONVERSATIONAL_LIMIT:
        response = render_message("capability.unsupported_unavailable_firm", ctx.current_locale, params)
    else:
        response = await _build_bounded_conversational_reply(
            ctx,
            ctx.current_locale,
            intent=UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT,
            extra_user_ctx={
                "unsupported_capability": {
                    "key": capability.key,
                    "label": str(params["capability"]),
                    "capability": str(params["capability"]),
                    "followup_count": next_count,
                    "supported": str(params["supported"]),
                    "supported_alternatives": str(params["supported"]),
                    "safety_note": capability.safety_note,
                },
            },
        )
        if not response:
            response = render_message("capability.unsupported_unavailable_followup", ctx.current_locale, params)

    logger.info(
        "gate_unsupported_capability_followup",
        capability_key=capability.key,
        followup_count=next_count,
        firm=next_count > _FOLLOWUP_CONVERSATIONAL_LIMIT,
    )
    return {
        **ctx.gate_updates,
        "capability_boundary": updated_boundary,
        "direct_path_triggered": True,
        "final_response": response,
        "semantic_path_shape": "capability_boundary_followup",
        **_route_observability_updates(
            owner="guardrail",
            decision="unsupported_capability_followup",
        ),
    }


async def _stage_semantic_unsupported_capability(ctx: GateContext) -> dict[str, Any] | None:
    """Semantic fallback for unsupported capability boundaries not caught by registry phrases."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
        or not ctx.phrase_heavy_fastpath_allowed
    ):
        return None
    if detect_unsupported_capability(ctx.message_text) is not None:
        return None
    if _is_supported_banking_request(ctx.message_text):
        return None

    capability = await _semantic_unsupported_capability(ctx, ctx.message_text)
    if capability is None:
        return None

    params = unsupported_capability_params(capability, locale=ctx.current_locale)
    logger.info("gate_semantic_unsupported_capability", capability_key=capability.key)
    return {
        **ctx.gate_updates,
        "capability_boundary": CapabilityBoundary(key=capability.key, label=capability.label),
        "direct_path_triggered": True,
        "final_response": render_message("capability.unsupported_unavailable", ctx.current_locale, params),
        "semantic_path_shape": "semantic_unsupported_capability",
        **_route_observability_updates(
            owner="guardrail",
            decision="semantic_unsupported_capability",
        ),
    }
