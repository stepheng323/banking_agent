import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _build_direct_domain_task
from shared.types.conversation_sets import ScheduleQueryContract
from shared.types.read import ReadRequest

_SCHEDULE_DOMAIN_SIGNAL_RE = re.compile(r"(?iu)\b(?:schedul\w*|recurr\w*|standing\s+order)\b")


def _could_be_schedule_read_request(text: str) -> bool:
    """Detect only the domain boundary; response shape remains semantic."""
    normalized = " ".join((text or "").split())
    return bool(normalized and len(normalized) <= 180 and _SCHEDULE_DOMAIN_SIGNAL_RE.search(normalized))


def _semantic_schedule_read_request(route: Any) -> ReadRequest | None:
    request = getattr(route, "read_request", None)
    if isinstance(request, ReadRequest) and request.subject == "schedule":
        return request
    return None


def _build_direct_schedule_read_updates(
    ctx: GateContext,
    *,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
    source: str,
    owner: str = "semantic_router",
    path_shape: str | None = None,
    read_request: ReadRequest,
    schedule_contract: ScheduleQueryContract | None = None,
) -> RouteResolution:
    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain="schedule",
        mode=canonical_mode,
        read_request=read_request,
        schedule_contract=schedule_contract,
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
    """Leave free-form schedule reads to the canonical semantic-router call."""
    del ctx
    return None
