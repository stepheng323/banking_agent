from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.gateway.api.webhooks import flows_router, message_router, mono_router, telegram_router
from shared.config.settings import settings
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("gateway_service_starting", **build_runtime_status("gateway"))
    yield
    logger.info("gateway_service_shutting_down")


app = FastAPI(title="Gateway Service", lifespan=lifespan)


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors https://telegram.org https://web.telegram.org; "
        "base-uri 'none'; "
        "form-action 'none'"
    )
    if not settings.runtime.is_local:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(message_router)
app.include_router(mono_router)
app.include_router(flows_router)
app.include_router(telegram_router)

# Serve static files for Telegram Mini App
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/health")
async def health() -> dict[str, object]:
    """Health endpoint exposing runtime metadata."""
    return {
        "status": "healthy",
        "service": "gateway",
        "runtime": build_runtime_status("gateway"),
    }


@app.get("/ready")
async def readiness() -> dict[str, object]:
    """Readiness endpoint exposing service and transport state."""
    return {
        "status": "ready",
        "service": "gateway",
        "ingress_enabled": True,
        "runtime": build_runtime_status("gateway"),
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
