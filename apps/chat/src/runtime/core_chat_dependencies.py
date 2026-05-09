"""Compatibility wrapper for the renamed chat worker dependency module."""

from apps.chat.src.runtime import chat_worker_dependencies as _chat_worker_dependencies
from apps.chat.src.runtime.chat_worker_dependencies import *  # noqa: F403

logger = _chat_worker_dependencies.logger
_resolve_role_model = _chat_worker_dependencies._resolve_role_model

__all__ = [name for name in globals() if not name.startswith("__")]
