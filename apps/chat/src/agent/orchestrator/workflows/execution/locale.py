"""Locale helpers for execution workflows."""

from __future__ import annotations

from typing import cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize(state.loaded_context.get("language")).value)


__all__ = ["_state_locale"]
