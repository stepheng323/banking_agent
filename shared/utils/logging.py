import hashlib
import logging
import os
import sys
from typing import Any

import structlog

from shared.config.settings import settings

_LOGGING_CONFIGURED = False
_HANDLER_MARKER = "_banking_agent_structlog_handler"
_LOG_LEVELS = {
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "notset": logging.NOTSET,
}


def log_level_number(raw_level: str | None) -> int:
    """Return a stdlib log level for a configured level name."""
    normalized = (raw_level or "").strip().lower()
    return _LOG_LEVELS.get(normalized, logging.INFO)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def orchestrator_diagnostics_verbose() -> bool:
    """Return whether normal-path orchestrator diagnostic logs should emit at info."""
    return (
        bool(settings.orchestrator_verbose_logs)
        or bool(settings.readiness_verbose_events)
        or _env_flag("ORCHESTRATOR_VERBOSE_LOGS")
        or _env_flag("READINESS_VERBOSE_EVENTS")
    )


def log_orchestrator_diagnostic(logger: Any, event: str, **fields: Any) -> None:
    """Emit noisy orchestrator diagnostics at debug unless verbose/readiness logging is enabled."""
    if orchestrator_diagnostics_verbose():
        logger.info(event, **fields)
        return
    logger.debug(event, **fields)


# ANSI escape helpers (avoids Rich overhead on every log line)
_DIM = "\033[2m"
_RESET = "\033[0m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RED_BOLD = "\033[1;31m"
_TIME_COLOR = "\033[90m"  # Solid dark gray for readable line-by-line tracking
_MSG_COLOR = "\033[36m"  # Cyan for high contrast and readability

# Ordered prefix → tag mapping.  First match wins.
_COMPONENT_MAP: list[tuple[str, str]] = [
    ("apps.gateway.api.webhooks.whatsapp", "whatsapp"),
    ("apps.gateway.api.webhooks.telegram", "telegram"),
    ("apps.gateway.api.webhooks.mono", "mono"),
    ("apps.gateway.api.webhooks.flutterwave", "flutterwave"),
    ("apps.gateway", "gateway"),
    ("apps.transaction", "transaction"),
    ("apps.receipt", "receipt"),
    ("apps.chat.src.worker_main", "chat-worker"),
    ("apps.chat.src.queue_consumers", "chat-worker"),
    ("apps.chat.src.runtime", "chat-worker"),
    ("apps.chat.src.agent.assistant_profile", "orchestrator"),
    ("apps.chat.src.agent.orchestrator", "orchestrator"),
    ("banking.scheduling", "scheduler"),
    ("banking.transactions", "transactions"),
    ("banking.transfers", "transfers"),
    ("banking.accounts", "accounts"),
    ("banking.bills", "bills"),
    ("banking.support", "support"),
    ("banking.policy", "policy"),
    ("banking.presentation", "i18n"),
    ("shared.queue", "queue"),
    ("shared.cache", "cache"),
    ("shared.clients.whatsapp", "whatsapp"),
    ("shared.clients.telegram", "telegram"),
    ("shared.clients.providers", "providers"),
    ("shared.resilience", "resilience"),
    ("shared.observability", "observability"),
]

_SKIP_FIELDS = frozenset(
    {
        "event",
        "level",
        "logger",
        "timestamp",
        "_record",
        "_from_structlog",
        "runtime_name",
        "app_env",
        "environment",
        "chat_transport",
        "async_transport",
        "infrastructure_environment",
        "project_name",
    }
)
_MAX_VALUE_LEN = 120


def _resolve_component(log_name: str) -> str:
    """Map a dotted logger name to a short subsystem tag."""
    if not log_name:
        return ""
    if log_name == "__main__":
        return "chat-worker"
    for prefix, tag in _COMPONENT_MAP:
        if log_name.startswith(prefix):
            return tag
    # Fallback: last dotted segment
    return log_name.rsplit(".", 1)[-1]


def _format_kv(key: str, val: Any) -> str:
    """Format a single key=value pair with dimmed key."""
    if isinstance(val, str):
        if " " in val or "=" in val or '"' in val:
            escaped = val.replace('"', '\\"')
            s = f'"{escaped}"'
        else:
            s = val
    else:
        s = str(val)
    return f"{_DIM}{key}={_RESET}{s}"


def _humanize_event(event: str) -> str:
    """Convert snake_case event names to readable text."""
    return event.replace("_", " ")


# Component color mapping for easy line-by-line tracking
_COMPONENT_COLORS = {
    "gateway": "\033[36m",  # Cyan
    "whatsapp": "\033[32m",  # Green
    "telegram": "\033[34m",  # Blue
    "mono": "\033[35m",  # Magenta
    "flutterwave": "\033[33m",  # Yellow
    "transaction": "\033[31m",  # Red
    "receipt": "\033[33m",  # Yellow
    "chat-worker": "\033[35m",  # Magenta
    "scheduler": "\033[33m",  # Yellow
    "queue": "\033[35m",  # Magenta
    "cache": "\033[90m",  # Dark Gray (kept muted for low priority)
    "orchestrator": "\033[34m",  # Blue
}


def _color_for_component(component: str) -> str:
    """Return a consistent ANSI color code for the component."""
    if not component:
        return ""
    if component in _COMPONENT_COLORS:
        return _COMPONENT_COLORS[component]

    # Stable fallback color based on hashing component name (solid colors only)
    palette = [
        "\033[31m",  # Red
        "\033[32m",  # Green
        "\033[33m",  # Yellow
        "\033[34m",  # Blue
        "\033[35m",  # Magenta
        "\033[36m",  # Cyan
    ]
    idx = sum(ord(c) for c in component) % len(palette)
    return palette[idx]


class BeautifulConsoleRenderer:
    """Plain-ANSI renderer for clean, aligned local-dev console output."""

    def __call__(self, logger: Any, name: str, event_dict: dict[str, Any]) -> str:
        event = event_dict.get("event", "")
        level = event_dict.get("level", "info").upper()
        timestamp = event_dict.get("timestamp", "")
        logger_name = event_dict.get("logger", "")
        if not logger_name and "_record" in event_dict:
            logger_name = event_dict["_record"].name

        # Timestamp — fixed 8-char HH:MM:SS
        time_part = ""
        if timestamp:
            time_part = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp[:8]
        if not time_part:
            import datetime

            time_part = datetime.datetime.now().strftime("%H:%M:%S")

        component = _resolve_component(logger_name)

        # --- gate_resolution: keep Rich for this single diagnostic event ---
        if event == "gate_resolution":
            return self._render_gate_resolution(time_part, event_dict)

        # --- Normal log lines: plain ANSI ---
        human_event = _humanize_event(event)

        # Collect key=value pairs, suppress large payloads
        kv_parts: list[str] = []
        for k, v in event_dict.items():
            if k in _SKIP_FIELDS:
                continue
            rendered = str(v)
            if len(rendered) > _MAX_VALUE_LEN:
                continue
            kv_parts.append(_format_kv(k, v))

        kv_str = " ".join(kv_parts)

        # Colorize tag based on component
        if component:
            color = _color_for_component(component)
            tag = f"{color}[{component}]{_RESET}"
        else:
            tag = ""

        if level in ("ERROR", "CRITICAL"):
            line = f"{_DIM}{time_part}{_RESET} {tag} {_RED_BOLD}✖ {human_event}{_RESET}"
        elif level == "WARNING":
            line = f"{_DIM}{time_part}{_RESET} {tag} {_YELLOW}⚠ {human_event}{_RESET}"
        elif level == "DEBUG":
            # Keep everything dimmed for debug, but tag still gets its color if needed
            line = f"{_DIM}{time_part}{_RESET} {tag} {_DIM}{human_event}"
            if kv_str:
                line += f" {kv_str}"
            line += _RESET
            return line
        else:
            line = f"{_DIM}{time_part}{_RESET} {tag} {_MSG_COLOR}{human_event}{_RESET}"

        if kv_str:
            line += f" {kv_str}"

        return line

    @staticmethod
    def _render_gate_resolution(time_part: str, event_dict: dict[str, Any]) -> str:
        """Rich-formatted gate_resolution diagnostic (infrequent, benefits from structure)."""
        import io as _io

        from rich.console import Console as _Console

        matched_handler_id = event_dict.get("matched_handler_id", "None")
        matched_layer = event_dict.get("matched_layer", "None")
        routing_owner = event_dict.get("routing_owner", "None")
        routing_decision = event_dict.get("routing_decision", "None")
        executed_handler_ids = event_dict.get("executed_handler_ids", [])
        skipped_handlers = event_dict.get("skipped_handlers", [])

        buf = _io.StringIO()
        console = _Console(file=buf, force_terminal=True, width=120)

        console.print(
            f"[dim]{time_part}[/dim] [bold cyan][gate][/bold cyan] [bold yellow]Evaluated Routing Gate[/bold yellow]"
        )

        skipped_by_layer: dict[str, int] = {}
        for h in skipped_handlers:
            layer_name = h.get("layer", "UNKNOWN")
            skipped_by_layer[layer_name] = skipped_by_layer.get(layer_name, 0) + 1

        for h_id in executed_handler_ids:
            console.print(f"         [bold green][✔][/bold green] [bold white]Executed:[/bold white] {h_id}")

        for layer_name, count in skipped_by_layer.items():
            console.print(
                f"         [bold yellow][~][/bold yellow] [bold white]Skipped {count} in {layer_name}[/bold white]"
            )

        console.print(
            f"         [bold magenta][★][/bold magenta] [bold white]MATCHED:[/bold white] "
            f"{matched_handler_id} ({matched_layer})"
        )
        console.print(
            f"         [bold blue][Details][/bold blue] "
            f"routing_owner={routing_owner} | routing_decision={routing_decision}"
        )

        return buf.getvalue().rstrip("\n")


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
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer() if settings.runtime.is_production else BeautifulConsoleRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
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
    root_logger.setLevel(log_level_number(settings.log_level))

    for noisy in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _LOGGING_CONFIGURED = True


def get_logger(name: str):
    """Get a structured logger."""
    return structlog.get_logger(name)


def log_fingerprint(value: Any, length: int = 16) -> str:
    """Return a short stable hash for correlating sensitive values in logs."""
    if value is None or value == "":
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]
