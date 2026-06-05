"""Checkpoint serialization helpers for orchestrator graph state."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from langgraph.checkpoint.redis.jsonplus_redis import JsonPlusRedisSerializer

from shared.money import naira_to_json, to_naira

_DECIMAL_MARKER = "__banking_agent_decimal__"
_DECIMAL_VALUE = "value"


class OrchestratorRedisSerializer(JsonPlusRedisSerializer):
    """Redis serializer that preserves Decimal values in graph state.

    The upstream Redis JSON serializer falls back to constructor encoding for
    Decimal instances, which reconstructs them as Decimal("0"). Transfer and
    bill task payloads carry money values, so the orchestrator must encode them
    explicitly before checkpointing.
    """

    def _decimal_payload(self, value: Decimal) -> dict[str, str | bool]:
        serialized = naira_to_json(value)
        if serialized is None:
            raise TypeError("Invalid Decimal value is not JSON serializable")
        return {_DECIMAL_MARKER: True, _DECIMAL_VALUE: serialized}

    def _default_handler(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return self._decimal_payload(obj)
        return super()._default_handler(obj)

    def _preprocess_interrupts(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return self._decimal_payload(obj)
        return super()._preprocess_interrupts(obj)

    def _revive_if_needed(self, obj: Any) -> Any:
        if isinstance(obj, dict) and obj.get(_DECIMAL_MARKER) is True:
            value = obj.get(_DECIMAL_VALUE)
            try:
                decimal_value = to_naira(value)
            except (InvalidOperation, TypeError, ValueError):
                decimal_value = None
            if decimal_value is None:
                raise ValueError(f"Invalid serialized Decimal checkpoint value: {value!r}")
            return decimal_value
        return super()._revive_if_needed(obj)


__all__ = ["OrchestratorRedisSerializer"]
