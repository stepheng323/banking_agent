"""Core Banking Service main module."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.core.src.runtime_bootstrap import warm_runtime
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting Core Banking Service...")
    await warm_runtime()

    logger.info(
        "consumer_ownership_config",
        mode="api_only",
        run_message_consumer=False,
        run_stream_consumer=False,
        started=[],
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
async def health() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}
