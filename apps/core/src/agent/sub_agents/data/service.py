"""Data plan service for fetching and selecting data plans."""

import re
from typing import Any

from apps.core.src.agent.sub_agents.data.models import DataPlan
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataPlanService:
    """
    Deterministic data plan service.

    Handles plan fetching, filtering, and selection without LLM calls.
    Uses provider abstraction for easy provider swapping.
    """

    def __init__(self, bill_provider: BillPaymentProvider):
        self.provider = bill_provider
        self._cache: dict[str, list[DataPlan]] = {}

    async def get_plans(self, network: str) -> list[DataPlan]:
        """
        Get all data plans for a network.

        Args:
            network: Network provider (MTN, AIRTEL, GLO, 9MOBILE)

        Returns:
            List of DataPlan objects
        """
        network_upper = network.upper().strip()

        # Check cache first
        if network_upper in self._cache:
            return self._cache[network_upper]

        result = await self.provider.get_data_plans(network_upper)

        if not result.get("success"):
            logger.warning("get_plans_failed", network=network, error=result.get("error"))
            return []

        plans = []
        for item in result.get("plans", []):
            plan = self._parse_plan(item, network_upper)
            if plan:
                plans.append(plan)

        # Cache the results
        self._cache[network_upper] = plans
        return plans

    def _parse_plan(self, item: dict[str, Any], network: str) -> DataPlan | None:
        """Parse a plan item from provider response."""
        try:
            name = item.get("name", "")
            amount = item.get("amount")

            if not name or amount is None:
                return None

            # Try to extract size and validity from name
            size_gb = self._extract_size_gb(name)
            validity_days = self._extract_validity_days(name)

            return DataPlan(
                item_code=item.get("item_code", ""),
                biller_code=item.get("biller_code", ""),
                name=name,
                network=network,
                amount=int(amount),
                size_gb=size_gb,
                validity_days=validity_days,
            )
        except Exception as e:
            logger.warning("parse_plan_failed", item=item, error=str(e))
            return None

    def _extract_size_gb(self, name: str) -> float | None:
        """Extract data size in GB from plan name."""
        # Match patterns like "1GB", "500MB", "1.5GB"
        gb_match = re.search(r"(\d+(?:\.\d+)?)\s*GB", name, re.IGNORECASE)
        if gb_match:
            return float(gb_match.group(1))

        mb_match = re.search(r"(\d+)\s*MB", name, re.IGNORECASE)
        if mb_match:
            return float(mb_match.group(1)) / 1000

        return None

    def _extract_validity_days(self, name: str) -> int | None:
        """Extract validity period in days from plan name."""
        # Match patterns like "30 Days", "7Days", "1 Month"
        day_match = re.search(r"(\d+)\s*day", name, re.IGNORECASE)
        if day_match:
            return int(day_match.group(1))

        week_match = re.search(r"(\d+)\s*week", name, re.IGNORECASE)
        if week_match:
            return int(week_match.group(1)) * 7

        month_match = re.search(r"(\d+)\s*month", name, re.IGNORECASE)
        if month_match:
            return int(month_match.group(1)) * 30

        return None

    async def get_plans_by_budget(self, network: str, max_price: int) -> list[DataPlan]:
        """
        Get plans that fit within a budget.

        Args:
            network: Network provider
            max_price: Maximum price in Naira

        Returns:
            List of plans within budget, sorted by best value (GB per Naira)
        """
        all_plans = await self.get_plans(network)
        filtered = [p for p in all_plans if p.amount <= max_price]

        # Sort by value (GB per Naira), highest first
        def value_score(plan: DataPlan) -> float:
            if plan.size_gb and plan.amount > 0:
                # Factor in validity for better scoring
                validity_factor = (plan.validity_days or 1) / 30
                return (plan.size_gb / plan.amount) * validity_factor
            return 0

        return sorted(filtered, key=value_score, reverse=True)

    def get_best_plan_for_budget(self, plans: list[DataPlan], budget: int) -> DataPlan | None:
        """
        Get the best value plan for a given budget.

        Prioritizes:
        1. Longer validity
        2. Better GB/Naira ratio

        Args:
            plans: Pre-fetched list of plans
            budget: Maximum price in Naira

        Returns:
            Best matching plan or None
        """
        affordable = [p for p in plans if p.amount <= budget]
        if not affordable:
            return None

        def score(plan: DataPlan) -> tuple:
            validity = plan.validity_days or 1
            size = plan.size_gb or 0
            value = size / plan.amount if plan.amount > 0 else 0
            return (validity, value, size)

        return max(affordable, key=score)

    async def get_plans_by_validity(self, network: str, days: int) -> list[DataPlan]:
        """
        Get plans with matching or similar validity.

        Args:
            network: Network provider
            days: Target validity in days

        Returns:
            List of plans sorted by closest match to target validity
        """
        all_plans = await self.get_plans(network)

        # Filter plans with known validity
        with_validity = [p for p in all_plans if p.validity_days]

        # Sort by closest match to target days
        return sorted(with_validity, key=lambda p: abs((p.validity_days or 0) - days))

    def clear_cache(self, network: str | None = None):
        """Clear cached plans."""
        if network:
            self._cache.pop(network.upper(), None)
        else:
            self._cache.clear()
