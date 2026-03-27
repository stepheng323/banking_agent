"""Lambda entrypoint for Gateway webhook ingress."""

import asyncio

from mangum import Mangum

from apps.gateway.main import app


def _build_handler() -> Mangum:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    return Mangum(app)


handler = _build_handler()
