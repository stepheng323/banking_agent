"""Unit tests for handler registry."""

from unittest.mock import MagicMock

from apps.core.src.agent.shared.batch.workflow.handler_registry import (
    WorkflowHandlerRegistry,
)


class TestHandlerRegistry:
    """Tests for WorkflowHandlerRegistry."""

    def test_register_and_get(self):
        """Register a handler and retrieve it."""
        registry = WorkflowHandlerRegistry()
        handler = MagicMock()

        registry.register("transfer", handler)
        result = registry.get("transfer")

        assert result is handler

    def test_get_nonexistent(self):
        """Getting unregistered handler returns None."""
        registry = WorkflowHandlerRegistry()

        result = registry.get("nonexistent")

        assert result is None

    def test_has_registered(self):
        """Check if handler is registered."""
        registry = WorkflowHandlerRegistry()
        handler = MagicMock()
        registry.register("transfer", handler)

        assert registry.has("transfer") is True
        assert registry.has("airtime") is False

    def test_list_types(self):
        """List all registered executor types."""
        registry = WorkflowHandlerRegistry()
        registry.register("transfer", MagicMock())
        registry.register("airtime", MagicMock())
        registry.register("data", MagicMock())

        types = registry.list_types()

        assert set(types) == {"transfer", "airtime", "data"}

    def test_override_handler(self):
        """Registering same type overwrites previous handler."""
        registry = WorkflowHandlerRegistry()
        handler1 = MagicMock()
        handler2 = MagicMock()

        registry.register("transfer", handler1)
        registry.register("transfer", handler2)

        assert registry.get("transfer") is handler2
