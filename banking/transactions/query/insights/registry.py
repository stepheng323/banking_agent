"""Generalized insight registry for canonical insight handlers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from banking.transactions.query.contracts import SurfaceView
from banking.transactions.query.models.extraction import InsightSpecBase


@dataclass
class InsightDefinition:
    """Canonical registry entry for an insight type."""

    insight_type: str
    spec_type: type[InsightSpecBase]
    result_type: type[BaseModel]
    executor: Callable[..., Awaitable[Any]]
    formatter: Callable[[Any, str], str]
    surface_builder: Callable[[Any, str], SurfaceView]
    item_builder: Callable[[Any, str], list[dict[str, Any]]]
    evidence_resolver: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None


class InsightRegistry:
    """Registry holding all supported canonical insights."""

    def __init__(self) -> None:
        self._insights: dict[str, InsightDefinition] = {}

    def register(self, definition: InsightDefinition) -> None:
        """Register a new insight definition."""
        self._insights[definition.insight_type] = definition

    def get(self, insight_type: str) -> InsightDefinition | None:
        """Retrieve a registered definition."""
        return self._insights.get(insight_type)

    def all(self) -> dict[str, InsightDefinition]:
        """Return all registered definitions."""
        return self._insights


insight_registry = InsightRegistry()
