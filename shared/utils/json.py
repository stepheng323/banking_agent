"""JSON serialization helpers for infrastructure boundaries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from math import isfinite
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from shared.money import naira_to_json

JsonSafe = None | bool | int | float | str | list["JsonSafe"] | dict[str, "JsonSafe"]


def to_json_safe(value: Any) -> JsonSafe:
    """Recursively convert application values into strict JSON-safe values."""
    if value is None or isinstance(value, bool | int | str):
        return value

    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Non-finite float is not JSON serializable")
        return value

    if isinstance(value, Decimal):
        serialized = naira_to_json(value)
        if serialized is None:
            raise ValueError("Invalid Decimal value is not JSON serializable")
        return serialized

    if isinstance(value, datetime | date):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Enum):
        return to_json_safe(value.value)

    if isinstance(value, BaseModel):
        return to_json_safe(value.model_dump(mode="json"))

    if is_dataclass(value) and not isinstance(value, type):
        return to_json_safe(asdict(value))

    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [to_json_safe(item) for item in value]

    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="ignore")

    return str(value)


def json_dumps_safe(value: Any, **kwargs: Any) -> str:
    """Dump JSON after converting Decimals and rejecting non-finite numbers."""
    kwargs["allow_nan"] = False
    return json.dumps(to_json_safe(value), **kwargs)


def to_json_safe_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a mapping into a JSON-safe dict for JSON database columns."""
    safe = to_json_safe(value)
    if not isinstance(safe, dict):
        raise ValueError("Expected mapping to serialize as JSON object")
    return safe
