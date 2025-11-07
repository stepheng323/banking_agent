"""Serialization utilities."""

from typing import Any, Dict

from sqlalchemy.inspection import inspect as sa_inspect


def sqlalchemy_to_dict(model: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict of column values.

    Only includes mapped column attributes (excludes relationships and internals).
    """
    try:
        mapper = sa_inspect(model).mapper
        data: Dict[str, Any] = {}
        for column in mapper.column_attrs:
            key = column.key
            value = getattr(model, key)
            # Convert non-JSON-native scalar types to strings
            if isinstance(value, (bytes, bytearray)):
                data[key] = value.decode("utf-8", errors="ignore")
            else:
                try:
                    # Let JSON encoder handle primitives; fallback to str
                    _ = value is None or isinstance(value, (str, int, float, bool))
                    data[key] = value if _ else str(value)
                except Exception:
                    data[key] = None
        return data
    except Exception:
        # Fallback: best-effort safe dict without private fields
        raw = getattr(model, "__dict__", {})
        return {
            k: (v if (v is None or isinstance(v, (str, int, float, bool))) else str(v))
            for k, v in raw.items() if not k.startswith("_")
        }


