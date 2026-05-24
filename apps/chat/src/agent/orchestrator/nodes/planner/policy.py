"""Planner locale and policy helper functions."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import MetaIntent
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.i18n import LocaleManager, render_message, render_policy_notice
from shared.policy.service import capability_block_message
from shared.services.unsupported_capabilities import (
    detect_unsupported_capabilities,
    format_planner_alternatives,
    get_unsupported_capability_by_policy_label,
    unsupported_capability_label,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_EXECUTOR_LABELS = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "query": "transaction query",
    "account": "account actions",
    "support": "support request",
    "faq": "banking help",
    "beneficiary": "beneficiary management",
    "schedule": "scheduled transaction management",
}
SUPPORTED_EXECUTOR_LABELS_BY_LOCALE = {
    "pcm": {
        "transfer": "money transfer",
        "airtime": "airtime purchase",
        "data": "data purchase",
        "query": "transaction query",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "yo": {
        "transfer": "transfer owo",
        "airtime": "rira airtime",
        "data": "rira data",
        "query": "wiwa transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "ha": {
        "transfer": "transfer kudi",
        "airtime": "sayan airtime",
        "data": "sayan data",
        "query": "binciken transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "ig": {
        "transfer": "transfer ego",
        "airtime": "izuta airtime",
        "data": "izuta data",
        "query": "nyocha transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
}

TRANSACTION_DEFAULT_ACTIONS = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
}

SCHEDULE_ACTIONS = {
    "schedule_transfer",
    "recurring_transfer",
    "schedule_airtime",
    "recurring_airtime",
    "schedule_data",
    "recurring_data",
    "list_scheduled_transfers",
    "cancel_scheduled_transfer",
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
}

POLICY_ACTION_ALIASES = {
    ("support", "handle_request"): "collect_details",
    ("support", "report_issue"): "collect_details",
}

PLANNER_POLICY_DEFAULT_ACTIONS = {
    **TRANSACTION_DEFAULT_ACTIONS,
    "schedule": "schedule_transfer",
    "support": "collect_details",
    "faq": "answer_question",
}

META_RESPONSE_KEY_TO_INTENT: dict[str, MetaIntent] = {
    "conversational.identity": MetaIntent.IDENTITY,
    "conversational.brand_origin": MetaIntent.BRAND_ORIGIN,
    "conversational.capability_question": MetaIntent.CAPABILITIES,
    "conversational.out_of_scope": MetaIntent.LIMITS,
}


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


def _locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in SUPPORTED_EXECUTOR_LABELS_BY_LOCALE else "en"


def _supported_executor_label(executor: str, locale: str) -> str:
    return SUPPORTED_EXECUTOR_LABELS_BY_LOCALE.get(_locale_key(locale), {}).get(
        executor,
        SUPPORTED_EXECUTOR_LABELS[executor],
    )


def _unsupported_policy_label(label: str, locale: str) -> str:
    capability = get_unsupported_capability_by_policy_label(label)
    if capability is None:
        return label
    return unsupported_capability_label(capability, locale)


def _resolve_unsupported_alternatives(unsupported: list[str], *, locale: str | None = None) -> list[str]:
    """Resolve up to two unique alternatives for planner notices."""
    return format_planner_alternatives(unsupported, locale=locale)


def _build_locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    """Prepare loaded_context patch with updated locale."""
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _detected_locale_value(planner_output: Any) -> str | None:
    """Resolve detected locale value from planner output when present."""
    detected_language = getattr(planner_output, "detected_language", None)
    if not isinstance(detected_language, str) or not detected_language:
        return None
    return cast(str, LocaleManager.from_detection(detected_language).value)


def _build_policy_notice(message_text: str, planner_output: Any, locale: str = "en") -> str | None:
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


def _task_capability_target(task: Any) -> tuple[str, str] | None:
    executor = str(getattr(task, "executor", "") or "").strip()
    action = str(getattr(task, "action", "") or "").strip()
    parameters = getattr(task, "parameters", None)

    if action in SCHEDULE_ACTIONS:
        return "schedule", action
    if executor == "transfer" and action == "send_money" and (
        getattr(parameters, "schedule", None) or getattr(parameters, "scheduled", None)
    ):
        return "schedule", "schedule_transfer"

    if action:
        return executor, POLICY_ACTION_ALIASES.get((executor, action), action)

    default_action = PLANNER_POLICY_DEFAULT_ACTIONS.get(executor)
    if default_action is None:
        return None
    return executor, default_action


def _filter_capability_blocked_tasks(planner_output: Any, locale: str = "en") -> tuple[Any, list[str]]:
    """Remove transaction tasks blocked by runtime capability policy."""
    tasks = list(getattr(planner_output, "tasks", []) or [])
    if not tasks:
        return planner_output, []

    kept_tasks = []
    blocked_messages: list[str] = []
    for task in tasks:
        executor = str(getattr(task, "executor", "") or "").strip()
        if executor not in PLANNER_POLICY_DEFAULT_ACTIONS and executor != "transfer":
            kept_tasks.append(task)
            continue

        capability_target = _task_capability_target(task)
        if not capability_target:
            kept_tasks.append(task)
            continue
        capability_domain, capability_action = capability_target

        block_message = capability_block_message(domain=capability_domain, action=capability_action, locale=locale)
        if block_message:
            logger.info(
                "planner_task_capability_blocked",
                executor=executor,
                domain=capability_domain,
                action=capability_action,
            )
            if block_message not in blocked_messages:
                blocked_messages.append(block_message)
            continue
        kept_tasks.append(task)

    if len(kept_tasks) == len(tasks):
        return planner_output, []

    return planner_output.model_copy(update={"tasks": kept_tasks}), blocked_messages


def _build_policy_aware_greeting(locale: str) -> str:
    return cast(str, render_message("conversational.greeting", locale))


def _meta_intent_from_response_key(response_key: str | None) -> MetaIntent | None:
    if not response_key:
        return None
    return META_RESPONSE_KEY_TO_INTENT.get(response_key)


__all__ = [
    "_build_locale_update",
    "_build_policy_aware_greeting",
    "_build_policy_notice",
    "_detected_locale_value",
    "_detect_unsupported_capabilities",
    "_filter_capability_blocked_tasks",
    "_meta_intent_from_response_key",
    "_resolve_unsupported_alternatives",
]
