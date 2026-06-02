"""Safe LangSmith/LangChain trace metadata helpers."""

from __future__ import annotations

import hashlib
from typing import Any

from shared.config.settings import settings
from shared.observability.redaction import redacted_dict
from shared.utils.logging import log_fingerprint


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


async def ainvoke_with_config(runnable: Any, input_value: Any, *, config: dict[str, Any] | None = None) -> Any:
    """Invoke a runnable with LangChain config, falling back for simple test doubles."""
    if not config:
        return await runnable.ainvoke(input_value)
    try:
        return await runnable.ainvoke(input_value, config=config)
    except TypeError as exc:
        message = str(exc)
        if "config" not in message or "unexpected keyword" not in message:
            raise
        return await runnable.ainvoke(input_value)
