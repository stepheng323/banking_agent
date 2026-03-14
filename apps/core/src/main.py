"""Core Banking Service main module."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.core.src.runtime_bootstrap import warm_runtime
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting Core Banking Service...", **build_runtime_status("core-api"))
    await warm_runtime()

    logger.info(
        "runtime_ownership_config",
        **build_runtime_status("core-api"),
    )

    yield

    logger.info("Shutting down Core Banking Service...")
    logger.info("Services stopped")


app = FastAPI(title="Core Banking Service", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint"""
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health() -> dict[str, object]:
    """Health check endpoint"""
    return {"status": "healthy", "service": "core-api", "stack_role": build_runtime_status("core-api")["stack_role"]}


@app.get("/ready")
async def readiness() -> dict[str, object]:
    """Readiness endpoint exposing API ownership mode."""
    return {
        "status": "ready",
        "service": "core-api",
        "ownership": build_runtime_status("core-api"),
    }
