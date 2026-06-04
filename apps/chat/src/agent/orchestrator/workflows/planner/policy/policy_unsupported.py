"""Unsupported-capability notice helpers for planner policy flows."""

from typing import Any, cast

from apps.chat.src.agent.assistant_profile.loader import get_cached_assistant_profile
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capabilities,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    format_planner_alternatives,
    unsupported_capability_label,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    get_unsupported_capability_by_policy_label,
)
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import (
    SUPPORTED_EXECUTOR_LABELS,
    _supported_executor_label,
)
from banking.presentation.i18n.bridge import render_policy_notice
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _detect_unsupported_capabilities(message_text: str) -> list[str]:
    """Resolve unsupported capabilities from planner-owned phrase patterns."""
    profile = get_cached_assistant_profile()
    configured_unsupported = profile.unsupported_capabilities
    detected_set = {
        capability.policy_label
        for capability in detect_unsupported_capabilities(
            message_text,
            allowed_policy_labels=configured_unsupported,
        )
    }

    # Deterministic order for stable output/tests.
    ordered_detected = [cap for cap in configured_unsupported if cap in detected_set]
    return ordered_detected


def _unsupported_policy_label(label: str, locale: str) -> str:
    capability = get_unsupported_capability_by_policy_label(label)
    if capability is None:
        return label
    return unsupported_capability_label(capability, locale)


def _resolve_unsupported_alternatives(unsupported: list[str], *, locale: str | None = None) -> list[str]:
    """Resolve up to two unique alternatives for planner notices."""
    return format_planner_alternatives(unsupported, locale=locale)


def _build_policy_notice(message_text: str, planner_output: PlannerOutput, locale: str = "en") -> str | None:
    if not planner_output or not planner_output.tasks:
        return None

    unsupported = _detect_unsupported_capabilities(message_text)
    if not unsupported:
        return None
    logger.info("unsupported_detected", capabilities=unsupported)

    supported_labels = []
    for executor in {t.executor for t in planner_output.tasks if t.executor in SUPPORTED_EXECUTOR_LABELS}:
        supported_labels.append(_supported_executor_label(executor, locale))

    if not supported_labels:
        return None

    supported_text = ", ".join(sorted(supported_labels))
    unsupported_text = ", ".join(_unsupported_policy_label(label, locale) for label in unsupported)
    alternatives = _resolve_unsupported_alternatives(unsupported, locale=locale)
    return cast(
        str,
        render_policy_notice(
            locale=locale,
            supported_text=supported_text,
            unsupported_text=unsupported_text,
            alternatives=alternatives,
        ),
    )


__all__ = [
    "_build_policy_notice",
    "_detect_unsupported_capabilities",
    "_resolve_unsupported_alternatives",
    "_unsupported_policy_label",
]
