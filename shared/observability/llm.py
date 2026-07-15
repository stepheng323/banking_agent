"""Safe LangSmith/LangChain trace metadata helpers."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any

from shared.config.settings import settings
from shared.observability.redaction import redacted_dict
from shared.utils.logging import log_fingerprint


class LLMCallDeadlineExceeded(TimeoutError):  # noqa: N818 - public contract name
    """Raised when an interactive LLM role exceeds its total turn deadline."""

    def __init__(self, *, role: str, deadline_seconds: float) -> None:
        self.role = role
        self.deadline_seconds = deadline_seconds
        super().__init__(f"{role} LLM call exceeded {deadline_seconds:g}s deadline")


def _sampled(key: str | None) -> bool:
    rate = max(0.0, min(float(settings.llm_trace_sample_rate), 1.0))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(str(key or "").encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") / 2**32
    return bucket < rate


def _hash_or_none(value: Any) -> str | None:
    return log_fingerprint(value) if value not in (None, "") else None


def build_llm_runnable_config(
    *,
    role: str,
    runtime: str = "chat-worker",
    channel: str | None = None,
    path_label: str | None = None,
    environment: str | None = None,
    phone_number: str | None = None,
    channel_identity: str | None = None,
    message_id: str | None = None,
    turn_id: str | None = None,
    locale: str | None = None,
    semantic_path_shape: str | None = None,
    task_domain: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build safe RunnableConfig tags/metadata for automatic LangSmith traces."""
    if not settings.llm_observability_enabled:
        return {}
    sampling_key = turn_id or message_id or phone_number or channel_identity or role
    if not _sampled(sampling_key):
        return {}

    tags = [
        f"runtime:{runtime}",
        f"env:{environment or settings.runtime.infrastructure_environment}",
        f"llm_role:{role}",
    ]
    if channel:
        tags.append(f"channel:{channel}")
    if path_label:
        tags.append(f"path:{path_label}")
    if task_domain:
        tags.append(f"domain:{task_domain}")

    metadata = {
        "privacy_mode": settings.llm_trace_privacy_mode,
        "runtime": runtime,
        "environment": environment or settings.runtime.infrastructure_environment,
        "llm_role": role,
        "channel": channel,
        "path_label": path_label,
        "phone_hash": _hash_or_none(phone_number),
        "channel_identity_hash": _hash_or_none(channel_identity),
        "message_id_hash": _hash_or_none(message_id),
        "turn_id_hash": _hash_or_none(turn_id),
        "locale": locale,
        "semantic_path_shape": semantic_path_shape,
        "task_domain": task_domain,
        **redacted_dict(extra_metadata),
    }
    return {"tags": tags, "metadata": {key: value for key, value in metadata.items() if value not in (None, "")}}


def with_llm_config(runnable: Any, **kwargs: Any) -> Any:
    """Attach safe LangChain config to a runnable when observability is enabled."""
    config = build_llm_runnable_config(**kwargs)
    if not config or not hasattr(runnable, "with_config"):
        return runnable
    return runnable.with_config(config)


async def ainvoke_with_config(
    runnable: Any,
    input_value: Any,
    *,
    config: dict[str, Any] | None = None,
    invocation_kwargs: Mapping[str, Any] | None = None,
    role: str | None = None,
    deadline_seconds: float | None = None,
) -> Any:
    """Invoke a runnable with LangChain config, falling back for simple test doubles."""
    resolved_role = role or _role_from_config(config) or "unknown"
    resolved_deadline = deadline_seconds
    if resolved_deadline is None:
        resolved_deadline = settings.llm_deadline_seconds(resolved_role)
    timeout_seconds = resolved_deadline or 0.0

    try:
        if timeout_seconds <= 0:
            return await _ainvoke_compat(
                runnable,
                input_value,
                config=config,
                invocation_kwargs=invocation_kwargs,
            )
        async with asyncio.timeout(timeout_seconds):
            return await _ainvoke_compat(
                runnable,
                input_value,
                config=config,
                invocation_kwargs=invocation_kwargs,
            )
    except TimeoutError as exc:
        raise LLMCallDeadlineExceeded(
            role=resolved_role,
            deadline_seconds=timeout_seconds,
        ) from exc


async def _ainvoke_compat(
    runnable: Any,
    input_value: Any,
    *,
    config: dict[str, Any] | None,
    invocation_kwargs: Mapping[str, Any] | None,
) -> Any:
    kwargs = dict(invocation_kwargs or {})
    if not config and not kwargs:
        return await runnable.ainvoke(input_value)
    try:
        if config:
            return await runnable.ainvoke(input_value, config=config, **kwargs)
        return await runnable.ainvoke(input_value, **kwargs)
    except TypeError as exc:
        message = str(exc)
        if not _is_unexpected_keyword_error(message):
            raise
        if config:
            try:
                return await runnable.ainvoke(input_value, config=config)
            except TypeError as config_exc:
                config_message = str(config_exc)
                if not _is_unexpected_keyword_error(config_message):
                    raise
        return await runnable.ainvoke(input_value)


def _role_from_config(config: Mapping[str, Any] | None) -> str | None:
    if not isinstance(config, Mapping):
        return None
    metadata = config.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    role = metadata.get("llm_role")
    return str(role) if isinstance(role, str) and role else None


def _is_unexpected_keyword_error(message: str) -> bool:
    return "unexpected keyword" in message or "got an unexpected keyword argument" in message
