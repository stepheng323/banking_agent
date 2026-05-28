"""Preflight decisions for orchestrator graph invocation."""

from dataclasses import dataclass
from typing import Any, Literal

from apps.chat.src.agent.orchestrator.guardrails.cancellation import cancel_match_kind, is_obvious_cancel_message
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.deterministic import (
    classify_deterministic_meta_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    classify_obvious_transfer_request,
)


@dataclass(frozen=True)
class InvocationPreflight:
    path_label: str = "planner_path"
    profile_mode: Literal["full", "minimal"] = "full"
    account_mode: Literal["full", "cache_only"] = "full"
    beneficiary_mode: Literal["full", "cache_only"] = "full"
    enable_initial_typing: bool = True


def plan_invocation_preflight(context: MessageContext, *, logger: Any) -> InvocationPreflight:
    if getattr(context, "is_media_input", False) or bool(context.image_data):
        return InvocationPreflight(path_label="media_path")

    cancel_kind = cancel_match_kind(context.text) if is_obvious_cancel_message(context.text) else None
    if cancel_kind:
        logger.info(
            "orchestrator_cancel_prefastpath",
            phone_number=context.phone_number,
            match_kind=cancel_kind,
        )
        return InvocationPreflight(
            path_label="cancel_path",
            profile_mode="minimal",
            account_mode="cache_only",
            beneficiary_mode="cache_only",
            enable_initial_typing=False,
        )

    if classify_obvious_transfer_request(context.text):
        return InvocationPreflight(
            path_label="direct_path",
            profile_mode="minimal",
            account_mode="full",
            beneficiary_mode="cache_only",
            enable_initial_typing=False,
        )

    if context.quoted_message_id:
        return InvocationPreflight()

    meta_response = classify_deterministic_meta_response(context.text)
    if meta_response:
        logger.info(
            "orchestrator_meta_prefastpath",
            phone_number=context.phone_number,
            response_key=meta_response.response_key,
            response_locale=meta_response.response_locale,
        )
        return InvocationPreflight(
            path_label="direct_path",
            profile_mode="minimal",
            account_mode="cache_only",
            beneficiary_mode="cache_only",
            enable_initial_typing=False,
        )

    return InvocationPreflight()
