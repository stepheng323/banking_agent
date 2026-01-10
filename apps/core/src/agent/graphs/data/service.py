"""Data plan service for fetching and selecting data plans."""

import json
import re
from typing import Any

import redis.asyncio as redis

from apps.core.src.agent.graphs.data.models import DataPlan
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600


class DataPlanService:
    """
    Deterministic data plan service.

    Handles plan fetching, filtering, and selection without LLM calls.
    Uses provider abstraction for easy provider swapping.
    Caches plans in Redis with TTL.
    """

    def __init__(self, bill_provider: BillPaymentProvider, redis_client: redis.Redis):
        self.provider = bill_provider
        self.redis = redis_client

    def _cache_key(self, network: str) -> str:
        return f"data_plans:{network.upper()}"

    async def get_plans(self, network: str) -> list[DataPlan]:
        """
        Get all data plans for a network.

        Args:
            network: Network provider (MTN, AIRTEL, GLO, 9MOBILE)

        Returns:
            List of DataPlan objects
        """
        network_upper = network.upper().strip()
        cache_key = self._cache_key(network_upper)

        cached = await self.redis.get(cache_key)
        if cached:
            try:
                plans_data = json.loads(cached)
                return [DataPlan(**p) for p in plans_data]
            except Exception as e:
                logger.warning("cache_parse_failed", error=str(e))

        result = await self.provider.get_data_plans(network_upper)

        if not result.get("success"):
            logger.warning("get_plans_failed", network=network, error=result.get("error"))
            return []

        plans = []
        for item in result.get("plans", []):
            plan = self._parse_plan(item, network_upper)
            if plan:
                plans.append(plan)

        if plans:
            plans_json = json.dumps([p.model_dump() for p in plans])
            await self.redis.setex(cache_key, CACHE_TTL, plans_json)

        return plans

    def _parse_plan(self, item: dict[str, Any], network: str) -> DataPlan | None:
        """Parse a plan item from provider response."""
        try:
            name = item.get("name", "")
            amount = item.get("amount")

            if not name or amount is None:
                return None

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
        gb_match = re.search(r"(\d+(?:\.\d+)?)\s*GB", name, re.IGNORECASE)
        if gb_match:
            return float(gb_match.group(1))

        mb_match = re.search(r"(\d+)\s*MB", name, re.IGNORECASE)
        if mb_match:
            return float(mb_match.group(1)) / 1000

        return None

    def _extract_validity_days(self, name: str) -> int | None:
        """Extract validity period in days from plan name."""
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

        def value_score(plan: DataPlan) -> float:
            if plan.size_gb and plan.amount > 0:
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
        with_validity = [p for p in all_plans if p.validity_days]
        return sorted(with_validity, key=lambda p: abs((p.validity_days or 0) - days))

    async def clear_cache(self, network: str | None = None):
        """Clear cached plans from Redis."""
        if network:
            await self.redis.delete(self._cache_key(network))
        else:
            keys = await self.redis.keys("data_plans:*")
            if keys:
                await self.redis.delete(*keys)
