"""Generic follow-up handling for frame-backed assistant responses."""

import re
import time
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.types.planner import ContextFrameFollowupDecision

CONTEXT_READ_LIST_LIMIT = 5
CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE = 0.55
_SENSITIVE_KEYS = {"pin", "otp", "password", "token", "secret"}
_LOOKUP_STOPWORDS = {
    "a",
    "about",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "bank",
    "beneficiaries",
    "beneficiary",
    "check",
    "did",
    "do",
    "does",
    "else",
    "for",
    "have",
    "how",
    "i",
    "is",
    "it",
    "list",
    "more",
    "my",
    "of",
    "one",
    "other",
    "recipients",
    "saved",
    "show",
    "that",
    "the",
    "then",
    "this",
    "those",
    "view",
    "what",
    "with",
    "you",
}
_SEARCHABLE_DATA_KEYS = (
    "alias",
    "name",
    "account_name",
    "bank_name",
    "bank",
    "category",
    "merchant",
    "network",
    "group_by",
    "group_key",
    "account_number",
    "account",
    "counterparty",
    "description",
    "direction",
    "recipient_name",
    "recipient_resolved_name",
    "reference",
    "status",
    "transaction_type",
    "type",
)


@dataclass(frozen=True, slots=True)
class ContextFrameFollowupResponse:
    response: str
    semantic_path_shape: str = "context_frame_followup"
    recent_domain_focus: str | None = None
    context_frames: list[ContextFrame] | None = None


@dataclass(frozen=True, slots=True)
class SurfaceAnswerRequest:
    state: OrchestratorState
    text: str
    decision: ContextFrameFollowupDecision | None = None
    locale: str = "en"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip("?.!,")


def _lookup_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in _LOOKUP_STOPWORDS]


def _frame_noun(frame_type: ContextFrameType, *, plural: bool) -> str:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "saved beneficiaries" if plural else "saved beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "linked accounts" if plural else "linked account"
    if frame_type == ContextFrameType.TRANSACTION_LIST:
        return "transactions or results" if plural else "transaction or result"
    if frame_type == ContextFrameType.TRANSACTION_DETAIL:
        return "transactions" if plural else "transaction"
    if frame_type == ContextFrameType.RECEIPT:
        return "receipt"
    return "items" if plural else "item"


def _frame_domain(frame_type: ContextFrameType) -> str | None:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "account"
    if frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL, ContextFrameType.RECEIPT}:
        return "query"
    return None


def _format_completeness_response(frame: ContextFrame) -> str | None:
    count = len(frame.items)
    if count <= 0:
        return None
    if count == 1:
        return f"Yes. That's the only {_frame_noun(frame.frame_type, plural=False)} I found."
    return f"Yes. Those are the {count} {_frame_noun(frame.frame_type, plural=True)} I found."


def _display_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def _candidate_detail_fields(entity: ContextEntity) -> list[tuple[str, Any]]:
    data = entity.data if isinstance(entity.data, dict) else {}
    if entity.entity_type.value == "beneficiary":
        keys = ("account_name", "name", "bank_name", "bank", "account_number", "account")
    elif entity.entity_type.value == "account":
        keys = ("bank_name", "account_number", "mandate_status", "available_balance", "balance")
    elif entity.entity_type.value == "transaction":
        keys = (
            "amount",
            "date",
            "counterparty",
            "description",
            "bank_name",
            "bank",
            "transaction_type",
            "type",
            "direction",
            "status",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "reference",
            "narration",
        )
    else:
        keys = (
            "summary",
            "description",
            "status",
            "amount",
            "count",
            "date",
            "bank_name",
            "bank",
            "group_by",
            "group_key",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "transaction_type",
            "type",
        )

    fields: list[tuple[str, Any]] = []
    for key in keys:
        if key in _SENSITIVE_KEYS:
            continue
        value = data.get(key)
        if value is None or value == "":
            continue
        fields.append((_display_key(key), value))
    return fields


def _format_detail_block(entity: ContextEntity, *, ordinal: int | None = None) -> str | None:
    header = entity.label or "Item"
    if ordinal is not None:
        header = f"{ordinal}. {header}"

    lines = [header]
    for label, value in _candidate_detail_fields(entity):
        lines.append(f"{label}: {value}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def _searchable_text(entity: ContextEntity) -> str:
    parts = [entity.label or ""]
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in _SEARCHABLE_DATA_KEYS:
        value = data.get(key)
        if value is not None:
            parts.append(str(value))
    return _normalize(" ".join(parts))


def _find_matching_entities(frame: ContextFrame, lookup_query: str) -> list[ContextEntity]:
    query_tokens = _lookup_tokens(lookup_query)
    if not query_tokens:
        return []

    matches: list[ContextEntity] = []
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if all(re.search(rf"\b{re.escape(token)}\b", searchable) for token in query_tokens):
            matches.append(entity)
    if matches:
        return matches

    # Fall back to any-token match so short references like "tolu" can bind to a list
    # where the visible labels carry branch qualifiers.
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if any(re.search(rf"\b{re.escape(token)}\b", searchable) for token in query_tokens):
            matches.append(entity)
    return matches


def _detail_header(frame: ContextFrame) -> str:
    if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "Saved Beneficiary Details" if len(frame.items) > 1 else "Beneficiary Details"
    if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
        return "Linked Account Details" if len(frame.items) > 1 else "Account Details"
    if frame.frame_type == ContextFrameType.TRANSACTION_LIST:
        return "Transaction Details"
    if frame.frame_type == ContextFrameType.RECEIPT:
        return "Receipt Details"
    return "Details"


def _format_details_response(frame: ContextFrame, text: str) -> str | None:
    del text
    if len(frame.items) > 1:
        blocks: list[str] = []
        for idx, entity in enumerate(frame.items[:CONTEXT_READ_LIST_LIMIT], 1):
            block = _format_detail_block(entity, ordinal=idx)
            if block:
                blocks.append(block)
        if not blocks:
            return None
        overflow = len(frame.items) - len(blocks)
        suffix = (
            f"\n\nShowing {len(blocks)} of {len(frame.items)} {_frame_noun(frame.frame_type, plural=True)}."
            if overflow > 0
            else ""
        )
        return f"{_detail_header(frame)}\n\n" + "\n\n".join(blocks) + suffix

    block = _format_detail_block(frame.items[0])
    if block is None:
        return None
    return f"{_detail_header(frame)}\n\n" + block


def _format_entity_details(frame: ContextFrame, entities: list[ContextEntity]) -> str | None:
    if not entities:
        return None
    if len(entities) == 1:
        block = _format_detail_block(entities[0])
        if block is None:
            return None
        return f"{_detail_header(frame)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        block = _format_detail_block(entity, ordinal=idx)
        if block:
            blocks.append(block)
    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(entities)} matches." if overflow > 0 else ""
    return f"{_detail_header(frame)}\n\n" + "\n\n".join(blocks) + suffix


def _format_lookup_response(frame: ContextFrame, lookup_query: str, *, explicit_lookup: bool) -> str | None:
    query_tokens = _lookup_tokens(lookup_query)
    if not query_tokens:
        return None

    matches = _find_matching_entities(frame, lookup_query)
    query_label = " ".join(query_tokens).title()

    if not matches:
        if not explicit_lookup:
            return None
        return f"I don't see {query_label} in the {_frame_noun(frame.frame_type, plural=True)} I showed."

    if len(matches) == 1:
        block = _format_detail_block(matches[0])
        if block is None:
            return f"Yes. {matches[0].label} is in the {_frame_noun(frame.frame_type, plural=True)} I showed."
        return f"{_detail_header(frame)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1):
        block = _format_detail_block(entity, ordinal=idx)
        if block:
            blocks.append(block)
    if not blocks:
        labels = "\n".join(f"{idx}. {entity.label}" for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1))
        return f"I found {len(matches)} matching {_frame_noun(frame.frame_type, plural=True)}:\n\n{labels}"

    overflow = len(matches) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(matches)} matches." if overflow > 0 else ""
    header = f"I found {len(matches)} matches in the {_frame_noun(frame.frame_type, plural=True)} I showed."
    return header + "\n\n" + "\n\n".join(blocks) + suffix


def _format_selection_response(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> str | None:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return _format_entity_details(frame, [frame.items[idx]])

    reference_text = (decision.reference_text or "").strip()
    if reference_text:
        return _format_lookup_response(frame, reference_text, explicit_lookup=True)
    return None


def _canonical_decision(decision: str) -> str:
    aliases = {
        "completeness_check": "answer_completeness",
        "entity_lookup": "lookup_entity",
        "detail_request": "show_details",
        "selection": "select_item",
        "new_task": "start_new_task",
    }
    return aliases.get(decision, decision)


def _format_filter_response(frame: ContextFrame, reference_text: str) -> str | None:
    matches = _find_matching_entities(frame, reference_text)
    if not matches:
        query_label = " ".join(_lookup_tokens(reference_text)).title()
        if not query_label:
            return None
        return f"I don't see {query_label} in the {_frame_noun(frame.frame_type, plural=True)} I showed."
    return _format_entity_details(frame, matches)


def _format_compare_response(frame: ContextFrame, reference_text: str | None) -> str | None:
    entities = _find_matching_entities(frame, reference_text) if reference_text else frame.items
    if len(entities) < 2:
        entities = frame.items
    if len(entities) < 2:
        return _format_entity_details(frame, entities)

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        fields = _candidate_detail_fields(entity)
        if not fields:
            blocks.append(f"{idx}. {entity.label}")
            continue
        lines = [f"{idx}. {entity.label}"]
        for label, value in fields:
            lines.append(f"{label}: {value}")
        blocks.append("\n".join(lines))

    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(entities)} items." if overflow > 0 else ""
    return "Comparison\n\n" + "\n\n".join(blocks) + suffix


def _format_explain_result_response(frame: ContextFrame) -> str | None:
    count = len(frame.items)
    if count <= 0:
        return None

    noun = _frame_noun(frame.frame_type, plural=count != 1)
    labels = [entity.label for entity in frame.items[:CONTEXT_READ_LIST_LIMIT] if entity.label]
    if not labels:
        return f"I showed {count} {noun} from the last result."

    label_text = ", ".join(labels)
    overflow = count - len(labels)
    suffix = f", and {overflow} more" if overflow > 0 else ""
    return f"I showed {count} {noun}: {label_text}{suffix}."


def _format_frame_clarification_response(frame: ContextFrame) -> str | None:
    domain = _frame_domain(frame.frame_type)
    if domain == "beneficiary":
        return "Are you asking about the saved beneficiaries I just showed?"
    if domain == "account":
        return "Are you asking about the linked accounts I just showed?"
    if domain == "query":
        return "Are you asking about the result I just showed?"
    return "Are you asking about the items I just showed?"


def _format_semantic_decision_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> str | None:
    if decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return None

    semantic_decision = _canonical_decision(decision.decision)

    if semantic_decision == "start_new_task":
        return None

    if semantic_decision == "answer_completeness":
        return _format_completeness_response(frame)

    if semantic_decision == "show_details":
        reference_text = (decision.reference_text or "").strip()
        if reference_text:
            matches = _find_matching_entities(frame, reference_text)
            if matches:
                return _format_entity_details(frame, matches)
        return _format_details_response(frame, "details")

    if semantic_decision == "lookup_entity":
        reference_text = (decision.reference_text or "").strip()
        if not reference_text:
            return _format_frame_clarification_response(frame)
        return _format_lookup_response(frame, reference_text, explicit_lookup=True)

    if semantic_decision == "filter_items":
        reference_text = (decision.reference_text or "").strip()
        if not reference_text:
            return _format_frame_clarification_response(frame)
        return _format_filter_response(frame, reference_text)

    if semantic_decision == "compare_items":
        return _format_compare_response(frame, (decision.reference_text or "").strip() or None)

    if semantic_decision == "select_item":
        return _format_selection_response(frame, decision)

    if semantic_decision == "explain_result":
        return _format_explain_result_response(frame)

    if semantic_decision == "unclear" and decision.confidence >= CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return _format_frame_clarification_response(frame)

    return None


def _refresh_context_frame(state: OrchestratorState, active_frame: ContextFrame) -> list[ContextFrame]:
    now = int(time.time())
    refreshed: list[ContextFrame] = []
    for frame in state.context_frames:
        if frame.frame_id == active_frame.frame_id:
            refreshed.append(frame.model_copy(update={"created_at_ts": now}))
        elif (frame.created_at_ts + frame.ttl_seconds) > now:
            refreshed.append(frame)
    return refreshed


class SurfaceAnswerEngine:
    """Ground conversational follow-ups against the latest displayed frame."""

    def build_context(self, frame: ContextFrame) -> str:
        """Build a compact LLM context for interpreting frame follow-ups."""
        lines = [f"Frame type: {frame.frame_type.value}", f"Item count: {len(frame.items)}", "Items:"]
        for idx, entity in enumerate(frame.items[:CONTEXT_READ_LIST_LIMIT], 1):
            data = entity.data if isinstance(entity.data, dict) else {}
            searchable_values = []
            for key in _SEARCHABLE_DATA_KEYS:
                value = data.get(key)
                if value is not None and value != "":
                    searchable_values.append(f"{key}={value}")
            suffix = f" | {'; '.join(searchable_values[:6])}" if searchable_values else ""
            lines.append(f"{idx}. {entity.label}{suffix}")
        overflow = len(frame.items) - CONTEXT_READ_LIST_LIMIT
        if overflow > 0:
            lines.append(f"... {overflow} more item(s) not shown in interpreter context")
        return "\n".join(lines)

    def answer(self, request: SurfaceAnswerRequest) -> ContextFrameFollowupResponse | None:
        """Answer a grounded follow-up from current state and a typed decision."""
        frame = OrchestratorContextManager().latest_active_frame(request.state)
        if frame is None or not frame.items or request.decision is None:
            return None

        response = _format_semantic_decision_response(frame, request.decision)
        if not response:
            return None
        return ContextFrameFollowupResponse(
            response=response,
            recent_domain_focus=_frame_domain(frame.frame_type),
            context_frames=_refresh_context_frame(request.state, frame),
        )


_SURFACE_ANSWER_ENGINE = SurfaceAnswerEngine()


def build_context_frame_followup_context(frame: ContextFrame) -> str:
    """Compatibility wrapper for existing frame-follow-up callers."""
    return _SURFACE_ANSWER_ENGINE.build_context(frame)


def build_context_frame_followup_response(
    state: OrchestratorState,
    text: str,
    *,
    decision: ContextFrameFollowupDecision | None = None,
) -> ContextFrameFollowupResponse | None:
    """Compatibility wrapper around SurfaceAnswerEngine."""
    return _SURFACE_ANSWER_ENGINE.answer(SurfaceAnswerRequest(state=state, text=text, decision=decision))


__all__ = [
    "ContextFrameFollowupResponse",
    "SurfaceAnswerEngine",
    "SurfaceAnswerRequest",
    "build_context_frame_followup_context",
    "build_context_frame_followup_response",
]
