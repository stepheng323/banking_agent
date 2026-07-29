"""Generalized insight registry for canonical insight handlers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from banking.transactions.query.contracts import SurfaceView
from banking.transactions.query.models.domain import QueryResultItem
from banking.transactions.query.models.operations import InsightSpecBase


@dataclass(frozen=True)
class InsightPresentation:
    """Atomic presentation produced from one authoritative insight result."""

    summary_text: str
    surface_view: SurfaceView
    items: list[QueryResultItem]


@dataclass
class InsightDefinition:
    """Canonical registry entry for an insight type."""

    insight_type: str
    spec_type: type[InsightSpecBase]
    result_type: type[BaseModel]
    executor: Callable[..., Awaitable[Any]]
    presenter: Callable[[Any, str], InsightPresentation]
    evidence_resolver: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None


def build_presentation(
    result: Any,
    language: str,
    *,
    formatter: Callable[[Any, str], str],
    surface_builder: Callable[[Any, str], SurfaceView],
    item_builder: Callable[[Any, str], list[dict[str, Any]]],
) -> InsightPresentation:
    """Adapt existing deterministic builders into one checked presentation."""
    summary_text = formatter(result, language)
    surface_view = surface_builder(result, language)
    raw_items = item_builder(result, language)
    if len(raw_items) != len(surface_view.items):
        raise ValueError("insight item and surface counts must match")
    items: list[QueryResultItem] = []
    for index, item in enumerate(raw_items):
        metadata = dict(item.get("metadata") or {})
        if index < len(surface_view.items):
            metadata["selection_payload"] = surface_view.items[index].payload.model_dump(mode="json")
        items.append(
            QueryResultItem(
                id=str(item["id"]),
                description=str(item["description"]),
                amount=float(item["amount"]),
                date=item["date"],
                metadata=metadata,
            )
        )
    return InsightPresentation(summary_text=summary_text, surface_view=surface_view, items=items)


class InsightRegistry:
    """Registry holding all supported canonical insights."""

    def __init__(self) -> None:
        self._insights: dict[str, InsightDefinition] = {}

    def register(self, definition: InsightDefinition) -> None:
        """Register a new insight definition."""
        if definition.insight_type in self._insights:
            raise ValueError(f"duplicate insight registration: {definition.insight_type}")
        self._insights[definition.insight_type] = definition

    def get(self, insight_type: str) -> InsightDefinition | None:
        """Retrieve a registered definition."""
        return self._insights.get(insight_type)

    def all(self) -> dict[str, InsightDefinition]:
        """Return all registered definitions."""
        return self._insights


insight_registry = InsightRegistry()
