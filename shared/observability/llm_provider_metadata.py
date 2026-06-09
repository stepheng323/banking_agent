"""Provider metadata extraction for LLM observability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def extract_provider_llm_metadata(raw_response: Any) -> dict[str, Any]:
    """Extract safe provider token/cache metadata from a raw LangChain response."""
    usage_metadata = _mapping_or_empty(getattr(raw_response, "usage_metadata", None))
    response_metadata = _mapping_or_empty(getattr(raw_response, "response_metadata", None))
    token_usage = _mapping_or_empty(response_metadata.get("token_usage"))

    input_tokens = _first_int(
        usage_metadata.get("input_tokens"),
        token_usage.get("prompt_tokens"),
        token_usage.get("input_tokens"),
    )
    output_tokens = _first_int(
        usage_metadata.get("output_tokens"),
        token_usage.get("completion_tokens"),
        token_usage.get("output_tokens"),
    )
    total_tokens = _first_int(
        usage_metadata.get("total_tokens"),
        token_usage.get("total_tokens"),
    )
    cached_tokens = _max_int(
        _nested_int(usage_metadata, ("input_token_details", "cached_tokens")),
        _nested_int(usage_metadata, ("input_token_details", "cache_read")),
        _nested_int(token_usage, ("prompt_tokens_details", "cached_tokens")),
        _nested_int(token_usage, ("input_tokens_details", "cached_tokens")),
        _nested_int(response_metadata, ("prompt_tokens_details", "cached_tokens")),
        _nested_int(response_metadata, ("input_tokens_details", "cached_tokens")),
    )
    reasoning_tokens = _max_int(
        _nested_int(usage_metadata, ("output_token_details", "reasoning")),
        _nested_int(usage_metadata, ("output_token_details", "reasoning_tokens")),
        _nested_int(token_usage, ("completion_tokens_details", "reasoning_tokens")),
        _nested_int(token_usage, ("output_tokens_details", "reasoning_tokens")),
        _nested_int(response_metadata, ("completion_tokens_details", "reasoning_tokens")),
        _nested_int(response_metadata, ("output_tokens_details", "reasoning_tokens")),
    )

    fields: dict[str, Any] = {}
    if input_tokens is not None:
        fields["provider_input_tokens"] = input_tokens
    if output_tokens is not None:
        fields["provider_output_tokens"] = output_tokens
    if total_tokens is not None:
        fields["provider_total_tokens"] = total_tokens
    if cached_tokens is not None:
        fields["provider_cached_tokens"] = cached_tokens
    if input_tokens and cached_tokens is not None:
        fields["provider_cache_hit_rate"] = round(cached_tokens / input_tokens, 4)
    if reasoning_tokens is not None:
        fields["provider_reasoning_tokens"] = reasoning_tokens

    system_fingerprint = _first_str(response_metadata.get("system_fingerprint"))
    finish_reason = _first_str(response_metadata.get("finish_reason"))
    request_id = _provider_request_id(response_metadata)
    if system_fingerprint:
        fields["provider_system_fingerprint"] = system_fingerprint
    if finish_reason:
        fields["provider_finish_reason"] = finish_reason
    if request_id:
        fields["provider_request_id"] = request_id
    return fields


def _provider_request_id(response_metadata: Mapping[str, Any]) -> str | None:
    direct = _first_str(
        response_metadata.get("request_id"),
        response_metadata.get("x-request-id"),
        response_metadata.get("openai-request-id"),
    )
    if direct:
        return direct
    headers = _mapping_or_empty(response_metadata.get("headers"))
    return _first_str(
        headers.get("x-request-id"),
        headers.get("openai-request-id"),
        headers.get("x-openai-request-id"),
        headers.get("request-id"),
    )


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _max_int(*values: int | None) -> int | None:
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None


def _nested_int(value: Mapping[str, Any], path: tuple[str, ...]) -> int | None:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, int) and not isinstance(current, bool) else None


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
