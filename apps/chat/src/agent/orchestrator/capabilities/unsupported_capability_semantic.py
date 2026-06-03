"""Semantic classifier prompts and validation for unsupported capabilities."""

from collections.abc import Mapping
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import (
    UNSUPPORTED_BOUNDARY_TURN_CONFIDENCE,
    UNSUPPORTED_CAPABILITY_SEMANTIC_CONFIDENCE,
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    ALLOWED_CAPABILITY_KEYS,
    UNSUPPORTED_CAPABILITY_REGISTRY,
    get_unsupported_capability,
)


def validate_semantic_unsupported_capability(
    decision: UnsupportedCapabilitySemanticOutput | Mapping[str, object],
    *,
    min_confidence: float = UNSUPPORTED_CAPABILITY_SEMANTIC_CONFIDENCE,
    allow_mixed: bool = False,
):
    parsed = (
        decision
        if isinstance(decision, UnsupportedCapabilitySemanticOutput)
        else UnsupportedCapabilitySemanticOutput.model_validate(decision)
    )
    if parsed.action == "mixed" and not allow_mixed:
        return None
    if parsed.action not in {"unsupported", "mixed"}:
        return None
    if parsed.confidence < min_confidence:
        return None
    key = (parsed.capability_key or "").strip()
    if key not in ALLOWED_CAPABILITY_KEYS:
        return None
    return get_unsupported_capability(key)


def validate_unsupported_boundary_turn(
    decision: UnsupportedBoundaryTurnOutput | Mapping[str, object],
    *,
    boundary_key: str,
    min_confidence: float = UNSUPPORTED_BOUNDARY_TURN_CONFIDENCE,
) -> UnsupportedBoundaryTurnOutput | None:
    parsed = (
        decision
        if isinstance(decision, UnsupportedBoundaryTurnOutput)
        else UnsupportedBoundaryTurnOutput.model_validate(decision)
    )
    if parsed.confidence < min_confidence:
        return None
    if parsed.action == "same_unsupported":
        key = (parsed.capability_key or boundary_key).strip()
        if key != boundary_key or key not in ALLOWED_CAPABILITY_KEYS:
            return None
        return parsed.model_copy(update={"capability_key": boundary_key})
    if parsed.action == "new_unsupported":
        key = (parsed.capability_key or "").strip()
        if key not in ALLOWED_CAPABILITY_KEYS:
            return None
        return parsed
    if parsed.action in {"supported_banking", "unrelated", "unclear"}:
        return parsed.model_copy(update={"capability_key": None})
    return None


def unsupported_capability_semantic_messages(
    *,
    text: str,
    locale: str | None = None,
    context: str = "None",
) -> list[dict[str, str]]:
    registry_lines = "\n".join(
        f"- {capability.key}: {capability.policy_label}; examples: {', '.join(capability.followup_terms[:5])}"
        for capability in UNSUPPORTED_CAPABILITY_REGISTRY
    )
    system_prompt = (
        "Classify whether the user asks for an unsupported capability for a Nigerian banking assistant.\n"
        "Return only one of the known registry keys. Do not invent categories.\n"
        "Known unsupported capabilities:\n"
        f"{registry_lines}\n\n"
        "Supported banking capabilities are local transfers, airtime/data purchase, balances, beneficiaries, "
        "scheduled transaction management, receipts/support for existing transactions, and transaction queries.\n"
        "Rules:\n"
        "- action=unsupported only when the whole turn is asking for one unsupported capability.\n"
        "- action=mixed only when the same turn clearly contains a supported banking request and an unsupported "
        "capability.\n"
        "- action=supported_or_other for supported banking, harmless chat, greetings, identity questions, or anything "
        "outside these unsupported categories.\n"
        "- action=unclear when the category is not clear.\n"
        "- Choose financial_advice for recommendations or 'what should I buy/sell' questions.\n"
        "- Choose investments for requests to buy, sell, trade, stake, or hold crypto, stocks, forex, or investments.\n"
        "- Choose lending for requests to borrow, get credit, obtain loans, or salary advances.\n"
        "- Choose international_transfers only for sending money across countries or foreign-currency transfer rails.\n"
        "- Choose pdf_exports/csv_exports for statement/history/receipt export or download requests.\n"
        "- Choose all_time_history only for all-time, lifetime, entire, or all-ever transaction history requests.\n"
        "- Be semantic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed language."
    )
    user_prompt = f'Locale hint: {locale or "unknown"}\nContext: {context or "None"}\nUser message: """{text}"""'
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_unsupported_capability_semantic(
    text: str,
    *,
    locale: str | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> UnsupportedCapabilitySemanticOutput:
    if structured_llm is None:
        return UnsupportedCapabilitySemanticOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="semantic_classifier_unavailable",
        )
    try:
        output = await structured_llm.ainvoke(
            unsupported_capability_semantic_messages(text=text, locale=locale, context=context)
        )
        return (
            output
            if isinstance(output, UnsupportedCapabilitySemanticOutput)
            else UnsupportedCapabilitySemanticOutput.model_validate(output)
        )
    except Exception:
        return UnsupportedCapabilitySemanticOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="semantic_classifier_failed",
        )


def unsupported_boundary_turn_messages(
    *,
    text: str,
    boundary_key: str,
    boundary_label: str,
    followup_count: int,
    locale: str | None = None,
    context: str = "None",
) -> list[dict[str, str]]:
    registry_lines = "\n".join(
        f"- {capability.key}: {capability.policy_label}" for capability in UNSUPPORTED_CAPABILITY_REGISTRY
    )
    system_prompt = (
        "Classify the user's next turn after a Nigerian banking assistant refused an unsupported capability.\n"
        "This is routing only; do not write the reply.\n"
        f"Active unsupported boundary: {boundary_key} ({boundary_label}).\n"
        "Known unsupported capabilities:\n"
        f"{registry_lines}\n\n"
        "Supported banking capabilities are local transfers, airtime/data purchase, balances, beneficiaries, "
        "scheduled transaction management, receipts/support for existing transactions, and transaction queries.\n"
        "Actions:\n"
        "- same_unsupported: user continues, pleads, negotiates, asks for an exception, offers repayment/benefit, "
        "or otherwise stays on the active unsupported topic even without naming it.\n"
        "- new_unsupported: user asks for a different known unsupported capability.\n"
        "- supported_banking: user makes a fresh supported banking request.\n"
        "- unrelated: user moves to harmless casual chat, identity, gratitude, or unrelated content.\n"
        "- unclear: you cannot tell.\n"
        "Never classify a fresh supported banking request as same_unsupported. Be semantic across English, Nigerian "
        "Pidgin, Yoruba, Hausa, Igbo, and mixed language."
    )
    user_prompt = (
        f"Locale hint: {locale or 'unknown'}\n"
        f"Follow-up count so far: {followup_count}\n"
        f"Context: {context or 'None'}\n"
        f'User message: """{text}"""'
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_unsupported_boundary_turn_semantic(
    text: str,
    *,
    boundary_key: str,
    boundary_label: str,
    followup_count: int = 0,
    locale: str | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> UnsupportedBoundaryTurnOutput:
    if structured_llm is None:
        return UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="boundary_turn_classifier_unavailable",
        )
    try:
        output = await structured_llm.ainvoke(
            unsupported_boundary_turn_messages(
                text=text,
                boundary_key=boundary_key,
                boundary_label=boundary_label,
                followup_count=followup_count,
                locale=locale,
                context=context,
            )
        )
        return (
            output
            if isinstance(output, UnsupportedBoundaryTurnOutput)
            else UnsupportedBoundaryTurnOutput.model_validate(output)
        )
    except Exception:
        return UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="boundary_turn_classifier_failed",
        )
