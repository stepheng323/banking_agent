import logging

from shared.config.settings import settings
from shared.utils.logging import (
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
