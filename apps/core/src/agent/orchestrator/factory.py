"""Adapter Factory."""

from typing import Any

from apps.core.src.agent.orchestrator.adapters.airtime import AirtimeSubgraphAdapter
from apps.core.src.agent.orchestrator.adapters.data import DataSubgraphAdapter
from apps.core.src.agent.orchestrator.protocols import SubgraphAdapter


class AdapterFactory:
    """Factory for creating domain adapters."""

    def __init__(self, services: dict[str, Any], user_cache=None):
        self.services = services
        self.user_cache = user_cache

    def get_adapter(self, executor: str) -> SubgraphAdapter | None:
        """Get adapter by executor name."""
        if executor == "transfer":
            return None
        elif executor == "airtime":
            return AirtimeSubgraphAdapter(self.services["airtime"], self.user_cache)
        elif executor == "data":
            return DataSubgraphAdapter(self.services["data"], self.user_cache)

        # TODO: Add other adapters (query, etc.)
        return None
