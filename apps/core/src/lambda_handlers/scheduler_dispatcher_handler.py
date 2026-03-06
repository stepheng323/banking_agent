"""Lambda handler for periodic schedule dispatch."""

import asyncio
from typing import Any

from apps.core.src.runtime.scheduler_dispatcher_dependencies import setup_schedule_dispatcher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _run_dispatch() -> dict[str, Any]:
    dispatcher = setup_schedule_dispatcher()
    stats = await dispatcher.dispatch_due()
    return {"ok": True, **stats}


def handler(event: dict, context: Any) -> dict:
    del event, context
    result = asyncio.run(_run_dispatch())
    logger.info("scheduler_dispatcher_tick_completed", result=result)
    return result
