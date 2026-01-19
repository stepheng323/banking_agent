"""Adapter Factory."""

from typing import Any

from apps.core.src.agent.orchestrator_graph.adapters.transfer import TransferSubgraphAdapter
from apps.core.src.agent.orchestrator_graph.protocols import SubgraphAdapter


class AdapterFactory:
    """Factory for creating domain adapters."""

    def __init__(self, services: dict[str, Any], user_cache=None):
        self.services = services
        self.user_cache = user_cache

    def get_adapter(self, executor: str) -> SubgraphAdapter | None:
        """Get adapter by executor name."""
        if executor == "transfer":
            return TransferSubgraphAdapter(
                self.services["transfer"], 
                self.user_cache
            )
        # TODO: Add other adapters (query, airtime, etc.)
        return None
