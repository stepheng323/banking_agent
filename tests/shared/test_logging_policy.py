import logging

from shared.config.settings import settings
from shared.utils.logging import (
    BeautifulConsoleRenderer,
    _humanize_event,
    _resolve_component,
    log_level_number,
    log_orchestrator_diagnostic,
    orchestrator_diagnostics_verbose,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.info_events: list[tuple[str, dict[str, object]]] = []
        self.debug_events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.info_events.append((event, fields))

    def debug(self, event: str, **fields: object) -> None:
        self.debug_events.append((event, fields))


def test_log_level_number_defaults_invalid_values_to_info() -> None:
    assert log_level_number("debug") == logging.DEBUG
    assert log_level_number("WARNING") == logging.WARNING
    assert log_level_number("unexpected") == logging.INFO


def test_orchestrator_diagnostics_emit_debug_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", False)
    monkeypatch.setattr(settings, "readiness_verbose_events", False)
    monkeypatch.delenv("ORCHESTRATOR_VERBOSE_LOGS", raising=False)
    monkeypatch.delenv("READINESS_VERBOSE_EVENTS", raising=False)

    logger = _FakeLogger()
    log_orchestrator_diagnostic(logger, "diagnostic_event", task_count=2)

    assert orchestrator_diagnostics_verbose() is False
    assert logger.info_events == []
    assert logger.debug_events == [("diagnostic_event", {"task_count": 2})]


def test_orchestrator_diagnostics_emit_info_when_verbose(monkeypatch) -> None:
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", True)
    monkeypatch.setattr(settings, "readiness_verbose_events", False)
    monkeypatch.delenv("READINESS_VERBOSE_EVENTS", raising=False)

    logger = _FakeLogger()
    log_orchestrator_diagnostic(logger, "diagnostic_event", task_count=2)

    assert orchestrator_diagnostics_verbose() is True
    assert logger.info_events == [("diagnostic_event", {"task_count": 2})]
    assert logger.debug_events == []


def test_orchestrator_diagnostics_emit_info_for_readiness_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", False)
    monkeypatch.setattr(settings, "readiness_verbose_events", False)
    monkeypatch.setenv("READINESS_VERBOSE_EVENTS", "true")

    logger = _FakeLogger()
    log_orchestrator_diagnostic(logger, "diagnostic_event", task_count=2)

    assert orchestrator_diagnostics_verbose() is True
    assert logger.info_events == [("diagnostic_event", {"task_count": 2})]
    assert logger.debug_events == []


def _render(event: str, *, level: str = "info", logger_name: str = "", **fields: object) -> str:
    """Helper to invoke the renderer with a minimal event_dict."""
    renderer = BeautifulConsoleRenderer()
    event_dict: dict[str, object] = {
        "event": event,
        "level": level,
        "timestamp": "2026-06-15T09:45:12.000000+01:00",
        "logger": logger_name,
        **fields,
    }
    return renderer(None, "", event_dict)


class TestResolveComponent:
    """Tests for the static component-name mapping."""

    def test_gateway_main(self) -> None:
        assert _resolve_component("apps.gateway.main") == "gateway"

    def test_whatsapp_webhook(self) -> None:
        assert _resolve_component("apps.gateway.api.webhooks.whatsapp.flows.router") == "whatsapp"

    def test_telegram_webhook(self) -> None:
        assert _resolve_component("apps.gateway.api.webhooks.telegram.service") == "telegram"

    def test_transaction_worker(self) -> None:
        assert _resolve_component("apps.transaction.main") == "transaction"

    def test_receipt_worker(self) -> None:
        assert _resolve_component("apps.receipt.main") == "receipt"

    def test_chat_worker_main(self) -> None:
        assert _resolve_component("__main__") == "chat-worker"

    def test_chat_worker_module(self) -> None:
        assert _resolve_component("apps.chat.src.worker_main") == "chat-worker"

    def test_orchestrator(self) -> None:
        assert _resolve_component("apps.chat.src.agent.orchestrator.workflows.gate.core.engine") == "orchestrator"

    def test_scheduler(self) -> None:
        assert _resolve_component("banking.scheduling.services.transaction_schedule_dispatcher") == "scheduler"

    def test_transfers(self) -> None:
        assert _resolve_component("banking.transfers.resolution.resolver") == "transfers"

    def test_shared_cache(self) -> None:
        assert _resolve_component("shared.cache.bank_cache") == "cache"

    def test_shared_whatsapp_client(self) -> None:
        assert _resolve_component("shared.clients.whatsapp.messages") == "whatsapp"

    def test_fallback_uses_last_segment(self) -> None:
        assert _resolve_component("some.unknown.module_name") == "module_name"

    def test_empty_returns_empty(self) -> None:
        assert _resolve_component("") == ""


class TestHumanizeEvent:
    def test_snake_case(self) -> None:
        assert _humanize_event("gateway_service_starting") == "gateway service starting"

    def test_no_underscores(self) -> None:
        assert _humanize_event("started") == "started"


class TestRendererOutput:
    def test_info_contains_component_tag(self) -> None:
        output = _render("service_starting", logger_name="apps.gateway.main")
        assert "[gateway]" in output
        assert "service starting" in output

    def test_info_contains_timestamp(self) -> None:
        output = _render("started", logger_name="apps.gateway.main")
        assert "09:45:12" in output

    def test_info_contains_kv_pairs(self) -> None:
        output = _render("loop_started", logger_name="apps.transaction.main", interval_seconds=300)
        assert "interval_seconds=" in output
        assert "300" in output

    def test_error_contains_marker(self) -> None:
        output = _render("something_failed", level="error", logger_name="apps.gateway.main")
        assert "✖" in output
        # Should contain red ANSI
        assert "\033[1;31m" in output or "\033[31m" in output

    def test_warning_contains_marker(self) -> None:
        output = _render("something_suspicious", level="warning", logger_name="apps.gateway.main")
        assert "⚠" in output
        assert "\033[33m" in output

    def test_debug_is_fully_dimmed(self) -> None:
        output = _render("tick_completed", level="debug", logger_name="apps.transaction.main")
        assert output.startswith("\033[2m")
        assert output.endswith("\033[0m")

    def test_large_payload_suppressed(self) -> None:
        big_list = list(range(200))
        output = _render("event", logger_name="apps.gateway.main", streams=big_list)
        assert "streams=" not in output

    def test_small_payload_kept(self) -> None:
        output = _render("event", logger_name="apps.gateway.main", count=42)
        assert "count=" in output
        assert "42" in output

    def test_no_rich_markup_in_normal_output(self) -> None:
        output = _render("service_starting", logger_name="apps.gateway.main", runtime_name="gateway")
        assert "[bold" not in output
        assert "[dim]" not in output
        assert "[/dim]" not in output
        assert "[cyan]" not in output
