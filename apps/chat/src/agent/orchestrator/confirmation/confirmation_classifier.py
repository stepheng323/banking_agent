"""Fastpath and LLM confirmation classification."""

from __future__ import annotations

from typing import Any

from shared.i18n.models import LocaleCode
from apps.chat.src.agent.orchestrator.confirmation.confirmation_guardrails import confirmation_guardrail_decision, is_safe_guarded_approval_text
from apps.chat.src.agent.orchestrator.confirmation.confirmation_models import (
    APPROVAL_CONFIDENCE_THRESHOLD,
    REJECTION_CONFIDENCE_THRESHOLD,
    ConfirmationDecision,
    ConfirmationDecisionOutput,
    ConfirmationPromptKind,
)
from apps.chat.src.agent.orchestrator.confirmation.confirmation_phrases import (
    CANCEL_PHRASES_BY_LOCALE,
    confirmation_approve_phrases,
    confirmation_locale_candidates,
    confirmation_reject_phrases,
    normalize_confirmation_locale,
    normalize_confirmation_text,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def classify_confirmation_reply_sync(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
    locale: str | LocaleCode | None = None,
) -> ConfirmationDecision:
    """Fast, deterministic confirmation classification with no fuzzy matching."""
    normalized = normalize_confirmation_text(text)
    guardrail = confirmation_guardrail_decision(normalized, prompt_kind)
    if guardrail is not None:
        return guardrail

    resolved_locale = normalize_confirmation_locale(locale)
    approve_phrases = confirmation_approve_phrases(prompt_kind)
    reject_phrases = confirmation_reject_phrases(prompt_kind)
    for candidate_locale in confirmation_locale_candidates(resolved_locale):
        if normalized in approve_phrases.get(candidate_locale, set()):
            return ConfirmationDecision("approve", "fastpath", 0.99, "phrase_approve")
        reject_set = set(reject_phrases.get(candidate_locale, set()))
        reject_set.update(CANCEL_PHRASES_BY_LOCALE.get(candidate_locale, set()))
        if normalized in reject_set:
            return ConfirmationDecision("reject", "fastpath", 0.99, "phrase_reject")

    return ConfirmationDecision("unclear", "fastpath", 0.0, "no_match")


def confirmation_decision_messages(
    *,
    text: str,
    prompt_kind: ConfirmationPromptKind,
    context: str,
) -> list[dict[str, str]]:
    system_prompt = (
        "Classify the user's reply to one active banking prompt. "
        "Return approve only when the user clearly agrees to the active prompt. "
        "Return reject when they decline, cancel, or defer. "
        "Return modify when they change amount, recipient, bank, account, source, or other details. "
        "Return new_request when they ask for a fresh banking task like a balance, transfer, airtime/data, "
        "receipt, beneficiary list, or transaction query. Return unclear when uncertain. "
        "Do not invent transaction facts."
    )
    user_prompt = (
        f"Prompt kind: {prompt_kind}\n"
        f"Context: {context or 'None'}\n"
        f"User reply: \"\"\"{text}\"\"\""
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_confirmation_reply(
    text: str,
    *,
    prompt_kind: ConfirmationPromptKind,
    locale: str | LocaleCode | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> ConfirmationDecision:
    """Classify a prompt reply with fastpath first and guarded LLM fallback."""
    fastpath = classify_confirmation_reply_sync(text, prompt_kind=prompt_kind, locale=locale)
    if fastpath.action != "unclear" or fastpath.reason != "no_match":
        return fastpath
    if structured_llm is None:
        return fastpath

    try:
        output = await structured_llm.ainvoke(
            confirmation_decision_messages(text=text, prompt_kind=prompt_kind, context=context)
        )
        parsed = (
            output
            if isinstance(output, ConfirmationDecisionOutput)
            else ConfirmationDecisionOutput.model_validate(output)
        )
    except Exception as exc:
        logger.warning("confirmation_decision_llm_failed", error=str(exc), prompt_kind=prompt_kind)
        return fastpath

    if parsed.action == "approve":
        if parsed.confidence < APPROVAL_CONFIDENCE_THRESHOLD:
            return ConfirmationDecision("unclear", "llm", parsed.confidence, "approval_below_threshold")
        if not is_safe_guarded_approval_text(text, prompt_kind=prompt_kind):
            return ConfirmationDecision("unclear", "guardrail", parsed.confidence, "unsafe_llm_approval")
        return ConfirmationDecision("approve", "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    if parsed.action == "reject":
        if parsed.confidence < REJECTION_CONFIDENCE_THRESHOLD:
            return ConfirmationDecision("unclear", "llm", parsed.confidence, "rejection_below_threshold")
        return ConfirmationDecision("reject", "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    if parsed.action in {"modify", "new_request"}:
        return ConfirmationDecision(parsed.action, "llm", parsed.confidence, parsed.reason, parsed.custom_data)

    return ConfirmationDecision("unclear", "llm", parsed.confidence, parsed.reason, parsed.custom_data)


__all__ = [
    "classify_confirmation_reply",
    "classify_confirmation_reply_sync",
    "confirmation_decision_messages",
]
