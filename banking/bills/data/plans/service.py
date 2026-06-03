"""Data plan service for fetching and selecting data plans."""

import json
import re
from typing import Any

import redis.asyncio as redis

from banking.bills.data.models.plans import DataPlan
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600
LAST_KNOWN_GOOD_TTL = 86400


def _normalized_plan_name(name: str) -> str:
    text = name.lower()
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:gb|g)\b", r"\1gb", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:mb|m)\b", r"\1mb", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe_data_plans(plans: list[DataPlan]) -> list[DataPlan]:
    """Collapse provider duplicates that render as the same plan to users."""
    deduped: list[DataPlan] = []
    seen: set[tuple[str, str, int, int | None]] = set()
    for plan in plans:
        key = (
            plan.network.strip().upper(),
            _normalized_plan_name(plan.name),
            int(plan.amount),
            plan.validity_days,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(plan)
    return deduped


class DataPlanService:
    """
    Deterministic data plan service.

    Handles plan fetching, filtering, and selection without LLM calls.
    Uses provider abstraction for easy provider swapping.
    Caches plans in Redis with TTL.
    """

    def __init__(self, bill_provider: BillPaymentProvider, redis_client: redis.Redis | None = None):
        self.provider = bill_provider
        self.redis = redis_client

    def _cache_key(self, network: str) -> str:
        return f"data_plans:{network.upper()}"

    def _last_known_good_key(self, network: str) -> str:
        return f"data_plans:lkg:{network.upper()}"

    async def _cache_get(self, key: str) -> str | bytes | None:
        if not self.redis:
            return None
        return await self.redis.get(key)

    async def _cache_setex(self, key: str, ttl: int, value: str) -> None:
        if self.redis:
            await self.redis.setex(key, ttl, value)

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

        cached = await self._cache_get(cache_key)
        if cached:
            try:
                plans_data = json.loads(cached)
                return dedupe_data_plans([DataPlan(**p) for p in plans_data])
            except Exception as e:
                logger.warning("cache_parse_failed", error=str(e))

        result = await self.provider.get_data_plans(network_upper)

        if not result.get("success"):
            logger.warning("get_plans_failed", network=network, error=result.get("error"))
            return await self._get_last_known_good_plans(network_upper)

        plans = []
        for item in result.get("plans", []):
            plan = self._parse_plan(item, network_upper)
            if plan:
                plans.append(plan)
        plans = dedupe_data_plans(plans)

        if plans:
            plans_json = json.dumps([p.model_dump() for p in plans])
            await self._cache_setex(cache_key, CACHE_TTL, plans_json)
            await self._cache_setex(self._last_known_good_key(network_upper), LAST_KNOWN_GOOD_TTL, plans_json)

        return plans

    async def _get_last_known_good_plans(self, network: str) -> list[DataPlan]:
        cached = await self._cache_get(self._last_known_good_key(network))
        if not cached:
            return []
        try:
            plans_data = json.loads(cached)
            plans = dedupe_data_plans([DataPlan(**p) for p in plans_data])
            logger.info("data_plans_last_known_good_used", network=network, count=len(plans))
            return plans
        except Exception as e:
            logger.warning("last_known_good_parse_failed", network=network, error=str(e))
            return []

    def _parse_plan(self, item: dict[str, Any], network: str) -> DataPlan | None:
        """Parse a plan item from provider response."""
        try:
            name = item.get("biller_name") or item.get("short_name") or item.get("name") or ""
            amount = item.get("amount")

            if not name or amount is None:
                return None

            size_gb = self._extract_size_gb(name)
            validity_days = self._extract_validity_days_from_value(item.get("validity_period"))
            if validity_days is None:
                validity_days = self._extract_validity_days(name)
            raw_metadata = self._safe_plan_metadata(item)
            tags = self._extract_tags(name, validity_days, raw_metadata)

            return DataPlan(
                item_code=item.get("item_code", ""),
                biller_code=item.get("biller_code", ""),
                name=name,
                network=network,
                amount=int(amount),
                size_gb=size_gb,
                validity_days=validity_days,
                tags=tags,
                raw_metadata=raw_metadata,
            )
        except Exception as e:
            logger.warning("parse_plan_failed", item=item, error=str(e))
            return None

    def _safe_plan_metadata(self, item: dict[str, Any]) -> dict[str, str | int | float | bool]:
        raw_item = item.get("raw_item")
        source = raw_item if isinstance(raw_item, dict) else item
        allowed = {
            "id",
            "item_code",
            "biller_code",
            "biller_name",
            "short_name",
            "amount",
            "validity_period",
            "category_name",
            "group_name",
            "country",
            "is_data",
        }
        return {
            str(key): value
            for key, value in source.items()
            if key in allowed and isinstance(value, str | int | float | bool)
        }

    def _extract_size_gb(self, name: str) -> float | None:
        """Extract data size in GB from plan name."""
        gb_match = re.search(r"(\d+(?:\.\d+)?)\s*GB", name, re.IGNORECASE)
        if gb_match:
            return float(gb_match.group(1))

        mb_match = re.search(r"(\d+)\s*MB", name, re.IGNORECASE)
        if mb_match:
            return float(mb_match.group(1)) / 1000

        return None

    def _extract_validity_days_from_value(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            days = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None

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

    def _extract_tags(
        self,
        name: str,
        validity_days: int | None,
        metadata: dict[str, str | int | float | bool],
    ) -> list[str]:
        text = " ".join(
            str(value or "")
            for value in (
                name,
                metadata.get("biller_name"),
                metadata.get("short_name"),
                metadata.get("category_name"),
                metadata.get("group_name"),
            )
        ).lower()
        tags: set[str] = set()
        if validity_days == 1 or "daily" in text:
            tags.add("daily")
        if validity_days == 7 or "weekly" in text or "week" in text:
            tags.add("weekly")
        if validity_days == 30 or "monthly" in text or "month" in text:
            tags.add("monthly")
        if "night" in text or "midnight" in text:
            tags.add("night")
        if "weekend" in text:
            tags.add("weekend")
        if any(token in text for token in ("social", "whatsapp", "facebook", "instagram", "tiktok", "x bundle")):
            tags.add("social")
        if any(token in text for token in ("youtube", "stream", "video")):
            tags.add("video")
        if "unlimited" in text:
            tags.add("unlimited")
        if "fair" in text and "use" in text:
            tags.add("fair_use")
        return sorted(tags)

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
            if self.redis:
                await self.redis.delete(self._cache_key(network), self._last_known_good_key(network))
        else:
            if self.redis:
                keys = await self.redis.keys("data_plans:*")
                if keys:
                    await self.redis.delete(*keys)
