import re

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_router import (
    build_router_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_types import TurnContextSummary

_DIRECT_CONTEXT_RECAP_EXACT = {
    "where did we stop",
    "what are we doing again",
    "what do you need again",
    "repeat that",
    "show it again",
}


def _should_invoke_semantic_router(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    return bool(normalized)


def _build_semantic_router_context(
    summary: TurnContextSummary,
    expected_executors: list[str],
    *,
    message_text: str,
) -> str:
    include_account_preview, include_beneficiary_preview = _semantic_router_preview_policy(message_text)
    return build_router_context_from_summary(
        summary,
        expected_executors=expected_executors,
        include_account_preview=include_account_preview,
        include_beneficiary_preview=include_beneficiary_preview,
    )


def _semantic_router_preview_policy(message_text: str) -> tuple[bool, bool]:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False, False

    include_accounts = bool(
        re.search(r"\b(?:account|accounts|bank|banks|balance|default|unlink|link|ready)\b", normalized)
    )
    include_beneficiaries = bool(re.search(r"\b(?:beneficiar|recipient|saved|alias)\b", normalized))
    return include_accounts, include_beneficiaries


def _build_direct_context_recap_response(summary: TurnContextSummary) -> str | None:
    if summary.active_flow_intent:
        next_step = "Continue with that flow."
        if summary.active_flow_missing_fields:
            next_step = f"Next step: provide {', '.join(summary.active_flow_missing_fields)}."
        return f"We are still in your {summary.active_flow_intent} flow. {next_step}"
    if summary.query_session_active:
        return "You are still viewing transaction results. You can refine the query or ask a follow-up."
    if summary.recent_answer_focus:
        return f"The last thing I showed was your {summary.recent_answer_focus.replace('_', ' ')}."
    return None


def _is_direct_context_recap_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    return normalized in _DIRECT_CONTEXT_RECAP_EXACT
