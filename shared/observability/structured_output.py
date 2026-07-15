"""Small compatibility helpers for observable structured LLM calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


def with_observable_structured_output(
    llm: Any,
    schema: type[Any],
    *,
    method: Literal["function_calling", "json_mode", "json_schema"] | None = None,
) -> Any:
    """Request the raw provider message alongside parsed structured output.

    The raw message carries usage and prompt-cache metadata. Older or fake model
    implementations may not support ``include_raw``; those retain the normal
    parsed-output behaviour.
    """

    kwargs: dict[str, Any] = {"include_raw": True}
    if method is not None:
        kwargs["method"] = method
    try:
        return llm.with_structured_output(schema, **kwargs)
    except TypeError as exc:
        if not _is_unsupported_include_raw_error(str(exc)):
            raise
        kwargs.pop("include_raw")
        try:
            return llm.with_structured_output(schema, **kwargs)
        except TypeError as method_exc:
            if method is None or not _is_unsupported_method_error(str(method_exc)):
                raise
            # Minimal test doubles and older LangChain integrations may expose
            # only ``with_structured_output(schema)``. Production clients keep
            # the requested method and raw telemetry path above.
            return llm.with_structured_output(schema)


def unpack_observable_structured_output(result: Any) -> tuple[Any | None, Any, Exception | None]:
    """Return ``(raw_message, parsed_value, parsing_error)`` from either shape."""

    if isinstance(result, Mapping) and (
        "raw" in result or "parsed" in result or "parsing_error" in result
    ):
        parsing_error = result.get("parsing_error")
        return (
            result.get("raw"),
            result.get("parsed"),
            parsing_error if isinstance(parsing_error, Exception) else None,
        )
    return None, result, None


def _is_unsupported_include_raw_error(message: str) -> bool:
    return "include_raw" in message and (
        "unexpected keyword" in message or "got an unexpected keyword argument" in message
    )


def _is_unsupported_method_error(message: str) -> bool:
    return "method" in message and (
        "unexpected keyword" in message or "got an unexpected keyword argument" in message
    )
