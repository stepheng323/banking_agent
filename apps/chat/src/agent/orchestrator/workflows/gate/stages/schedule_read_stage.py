import re
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _route_observability_updates,
    _semantic_route_decision,
    _semantic_route_mode,
)
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


def _build_direct_schedule_read_updates(
    ctx: GateContext,
    *,
    updates: dict[str, Any],
    schedule_response_mode: str,
    canonical_decision: str | None,
    canonical_mode: str | None,
    route_source: str,
) -> dict[str, Any]:
    task_id, spec = _build_direct_domain_task(
        state=ctx.state,
        domain="schedule",
        mode=canonical_mode,
        schedule_response_mode="count" if schedule_response_mode == "count" else "list",
    )
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "semantic_router_schedule_direct",
        **_route_observability_updates(
            owner="semantic_router",
            decision=canonical_decision or "domain_schedule",
            target_domain="schedule",
            mode=canonical_mode,
            route_source=route_source,
        ),
        **updates,
    }


async def _stage_schedule_read_router(ctx: GateContext) -> dict[str, Any] | None:
    """Use a small semantic classifier for simple scheduled-transaction read turns."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
        or ctx.task_planner is None
    ):
        return None
    if not _could_be_schedule_read_request(ctx.message_text):
        return None

    try:
        route = await ctx.task_planner.route_schedule_read_turn(
            ctx.state.phone_number,
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
        updates={"semantic_path_shape": "schedule_read_router_direct"},
        schedule_response_mode=schedule_response_mode,
        canonical_decision=canonical_decision,
        canonical_mode=_semantic_route_mode(route) or "new",
        route_source="schedule_read_router",
    )
