"""Compatibility wrappers for chat runtime dependency loaders."""

from typing import Any


def setup_chat_consumers() -> tuple[Any, Any]:
    from apps.chat.src.runtime.chat_worker_dependencies import setup_chat_consumers as _setup_chat_consumers

    return _setup_chat_consumers()


def setup_core_consumers() -> tuple[Any, Any]:
    return setup_chat_consumers()
