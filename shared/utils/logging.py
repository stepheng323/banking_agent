import hashlib
import logging
import sys
from typing import Any

import structlog

from shared.config.settings import settings

_LOGGING_CONFIGURED = False
_HANDLER_MARKER = "_banking_agent_structlog_handler"


def configure_logger() -> None:
    """Configure structured logging once per process."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.runtime.is_production
        else structlog.dev.ConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    root_logger = logging.getLogger()
    for existing_handler in list(root_logger.handlers):
        if getattr(existing_handler, _HANDLER_MARKER, False):
            root_logger.removeHandler(existing_handler)

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _LOGGING_CONFIGURED = True


def get_logger(name: str):
    """Get a structured logger."""
    return structlog.get_logger(name)


def log_fingerprint(value: Any, length: int = 16) -> str:
    """Return a short stable hash for correlating sensitive values in logs."""
    if value is None or value == "":
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]
