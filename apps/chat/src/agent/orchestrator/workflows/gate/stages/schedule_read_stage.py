import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import (
    _semantic_route_decision,
    _semantic_route_mode,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _build_direct_domain_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_SCHEDULE_READ_CANDIDATE_RE = re.compile(
    r"(?iu)(?:"
    r"\bschedul\w*\b|\brecurr\w*\b|\bpending\b.*\b(?:transaction|payment|transfer|airtime|data)\w*\b|"
    r"\bprogram(?:me|med|mes|ar|ado|ada|ados|adas|mé|mée|mées|més)\w*\b|"
    r"\betal[eè]\b|\betalement\b|\bprogramm[ée]s?\b|"
    r"\beto\b.*\b(?:isanwo|owo|transaction)\b|"
    r"\b(?:ti a se eto|san nigbamii|sisanwo ti n bo)\b|"
    r"\b(?:tsara|jadawali|maimaitawa|biyan)\b.*\b(?:kudi|ciniki|biya)\b|"
    r"\b(?:haziri|ugwo|mbufe|azumahia)\b.*\b(?:emechaa|na-abia|oge)\b"
    r")"
)
_SCHEDULE_READ_COUNT_RE = re.compile(r"(?iu)\b(?:how\s+many|count|number\s+of|do\s+i\s+have\s+any|any)\b")
_SCHEDULE_READ_LIST_RE = re.compile(
    r"(?iu)^\s*(?:show|list|view|get|display|see|find)\b.*"
    r"\b(?:schedul\w*|recurr\w*|pending\b.*\b(?:transaction|payment|transfer|airtime|data)\w*)\b"
)


def _could_be_schedule_read_request(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 180:
        return False
    return bool(_SCHEDULE_READ_CANDIDATE_RE.search(normalized))


def _semantic_schedule_response_mode(route: Any) -> str | None:
    mode = getattr(route, "schedule_response_mode", None)
    if mode in {"list", "count"}:
        return str(mode)
    return None


def _deterministic_schedule_response_mode(text: str) -> str | None:
    normalized = " ".join((text or "").split())
    if not _could_be_schedule_read_request(normalized):
        return None
    if _SCHEDULE_READ_COUNT_RE.search(normalized):
        return "count"
    if _SCHEDULE_READ_LIST_RE.search(normalized):
        return "list"
    return None


def _build_direct_schedule_read_updates(
    ctx: GateContext,
    *,
    updates: dict[str, Any],
    schedule_response_mode: str,
    canonical_decision: str | None,
    canonical_mode: str | None,
    source: str,
    owner: str = "semantic_router",
    path_shape: str | None = None,
) -> RouteResolution:
    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain="schedule",
        mode=canonical_mode,
        schedule_response_mode="count" if schedule_response_mode == "count" else "list",
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner=owner,
        decision=canonical_decision or "domain_schedule",
        path_shape=path_shape,
        extra_updates={**(ctx.summary_updates or {}), **updates},
        target_domain="schedule",
        mode=canonical_mode,
        source=source,
    )


async def _stage_schedule_read_router(ctx: GateContext) -> RouteResolution | None:
    """Use a small semantic classifier for simple scheduled-transaction read turns."""
    if ctx.live_pending_interrupt or ctx.state_view.has_gate_blocking_state:
        return None
    if not _could_be_schedule_read_request(ctx.message_text):
        return None

    deterministic_mode = _deterministic_schedule_response_mode(ctx.message_text)
    if deterministic_mode is not None:
        logger.info(
            "gate_deterministic_schedule_read_direct",
            schedule_response_mode=deterministic_mode,
        )
        return _build_direct_schedule_read_updates(
            ctx,
            updates={},
            schedule_response_mode=deterministic_mode,
            canonical_decision="deterministic_schedule_read",
            canonical_mode="new",
            source="schedule_read_guard",
            owner="guardrail",
            path_shape="deterministic_schedule_read",
        )

    router = ctx.semantic_router_llm
    if router is None:
        return None

    try:
        route = await router.route_schedule_read_turn(
            ctx.state_view.phone_number,
            ctx.message_text,
            path_label="direct_path",
        )
    except Exception as exc:
        logger.warning("gate_schedule_read_router_failed", error=str(exc))
        return None

    canonical_decision = _semantic_route_decision(route)
    schedule_response_mode = _semantic_schedule_response_mode(route)
    confidence = float(getattr(route, "confidence", 0.0) or 0.0)
    if canonical_decision != "domain_schedule" or schedule_response_mode is None or confidence < 0.72:
        return None

    logger.info(
        "gate_schedule_read_router_direct",
        decision=canonical_decision,
        schedule_response_mode=schedule_response_mode,
        confidence=round(confidence, 2),
    )
    return _build_direct_schedule_read_updates(
        ctx,
        updates={},
        schedule_response_mode=schedule_response_mode,
        canonical_decision=canonical_decision,
        canonical_mode=_semantic_route_mode(route) or "new",
        source="schedule_read_router",
        path_shape="schedule_read_router_direct",
    )
