"""Deterministic grounding for active query-result conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Literal, cast

from apps.chat.src.agent.graphs.query.models import Filters, QueryFrame, QueryResult, QueryResultItem
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.shared.query_contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_STOPWORDS = {
    "a",
    "about",
    "and",
    "for",
    "is",
    "it",
    "me",
    "my",
    "of",
    "one",
    "that",
    "the",
    "this",
    "to",
    "transaction",
    "what",
    "was",
    "were",
}
QueryFactField = Literal[
    "status",
    "amount",
    "recipient",
    "counterparty",
    "bank",
    "date",
    "description",
    "reference",
    "account",
    "direction",
    "category",
]
_FIELD_MAP: dict[str, QueryFactField] = {
    "status": "status",
    "amount": "amount",
    "recipient": "recipient",
    "counterparty": "counterparty",
    "bank": "bank",
    "date": "date",
    "description": "description",
    "reference": "reference",
    "account": "account",
    "direction": "direction",
    "category": "category",
}


@dataclass(frozen=True, slots=True)
class QuerySurfaceMatch:
    """A validated visible query-surface target."""

    item: SurfaceItemView
    query_item: QueryResultItem | None
    index: int
    frame_id: str | None = None


def _format_naira(value: float) -> str:
    value = abs(value)
    if value.is_integer():
        return f"₦{value:,.0f}"
    return f"₦{value:,.2f}"


def _amount_reference_values(text: str | None) -> set[float]:
    if not text:
        return set()
    values: set[float] = set()
    normalized = text.lower().replace(",", "")

    def add(raw: str, suffix: str | None = None) -> None:
        try:
            value = float(raw)
        except ValueError:
            return
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        values.add(value)

    for match in re.finditer(r"(?:₦|ngn|naira)\s*([0-9]+(?:\.[0-9]+)?)\s*([km])?\b", normalized):
        add(match.group(1), match.group(2))
    for match in re.finditer(r"\b([0-9]+(?:\.[0-9]+)?)\s*([km])\b", normalized):
        add(match.group(1), match.group(2))
    for match in re.finditer(r"\b[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?\b", text.lower()):
        add(match.group(0).replace(",", ""))
    for match in re.finditer(r"\b[0-9]{4,}(?:\.[0-9]+)?\b", normalized):
        add(match.group(0))
    return values


def _tokens(text: str | None) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(token) > 1 and token not in _STOPWORDS]


def _surface_item_text(item: SurfaceItemView) -> str:
    parts: list[str] = [item.label]
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    for key in (
        "description",
        "bank_name",
        "recipient_name",
        "recipient_bank_name",
        "counterparty",
        "transaction_type",
        "direction",
        "type",
        "status",
        "reference",
        "transaction_id",
        "source_account_number",
        "account_number",
        "category",
        "resolved_category",
    ):
        value = metadata.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).casefold()


def _normalize_text(value: str | None) -> str:
    return " ".join(_tokens(value))


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return date.today()
    return date.today()


def _snapshot_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if snapshot.get("date") is not None:
        metadata["date"] = snapshot.get("date")
    if snapshot.get("bank") is not None:
        metadata["bank_name"] = snapshot.get("bank")
    if snapshot.get("counterparty") is not None:
        metadata["counterparty"] = snapshot.get("counterparty")
        metadata["recipient_name"] = snapshot.get("counterparty")
    if snapshot.get("status") is not None:
        metadata["status"] = snapshot.get("status")
    if snapshot.get("direction") is not None:
        metadata["transaction_type"] = snapshot.get("direction")
        metadata["direction"] = snapshot.get("direction")
        metadata["type"] = snapshot.get("direction")
    if snapshot.get("absolute_position") is not None:
        metadata["absolute_position"] = snapshot.get("absolute_position")
    if snapshot.get("page_position") is not None:
        metadata["page_position"] = snapshot.get("page_position")
    return metadata


def _surface_item_from_snapshot(snapshot: dict[str, Any]) -> SurfaceItemView | None:
    entity_id = snapshot.get("id")
    label = str(snapshot.get("label") or "").strip()
    if not isinstance(entity_id, str) or not entity_id.strip() or not label:
        return None
    amount = snapshot.get("amount")
    return SurfaceItemView(
        id=entity_id,
        label=label,
        amount=float(amount) if isinstance(amount, (int, float)) else None,
        payload=SelectionPayload(
            selection_kind="transaction",
            entity_type="transaction",
            entity_id=entity_id,
            label=label,
        ),
        metadata=_snapshot_metadata(snapshot),
    )


def _query_item_from_surface_item(item: SurfaceItemView) -> QueryResultItem:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    return QueryResultItem(
        id=item.id,
        description=item.label,
        amount=float(item.amount or 0),
        date=_parse_date(metadata.get("date")),
        metadata=metadata,
    )


def _query_item_for_surface_item(query_result: QueryResult | None, item: SurfaceItemView) -> QueryResultItem | None:
    if query_result is None or not query_result.items:
        return None
    for query_item in query_result.items:
        if query_item.id == item.id:
            return query_item
    return None


def _surface_matches_amount(item: SurfaceItemView, amount_refs: set[float]) -> bool:
    if not amount_refs or item.amount is None:
        return False
    amount = abs(float(item.amount))
    return any(abs(amount - abs(target)) < 0.01 for target in amount_refs)


def _filter_matches(item: SurfaceItemView, filters: Filters | None) -> bool:
    if filters is None:
        return True
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    text = _surface_item_text(item)
    if filters.transaction_type:
        raw_type = str(metadata.get("transaction_type") or metadata.get("type") or "").casefold()
        if filters.transaction_type.casefold() not in raw_type:
            return False
    if filters.account_filter:
        if filters.account_filter.casefold() not in text:
            return False
    for values in (filters.counterparty, filters.merchant, filters.category):
        if values and not any(str(value).casefold() in text for value in values):
            return False
    if filters.min_amount is not None and (item.amount is None or abs(float(item.amount)) < filters.min_amount):
        return False
    if filters.max_amount is not None and (item.amount is None or abs(float(item.amount)) > filters.max_amount):
        return False
    return True


def _resolve_field(decision: Any) -> QueryFactField | None:
    requested = getattr(decision, "requested_field", None) or getattr(decision, "fact_field", None)
    if requested == "counterparty":
        return "counterparty"
    if requested in _FIELD_MAP:
        return _FIELD_MAP[cast(str, requested)]
    return None


def _target_index(decision: Any) -> int | None:
    raw = getattr(decision, "target_index", None)
    if isinstance(raw, int) and raw > 0:
        return raw - 1
    # Legacy field is accepted only as a candidate and still validated against the visible surface.
    raw = getattr(decision, "drill_down_index", None)
    if isinstance(raw, int) and raw >= 0:
        return raw
    return None


def _rank(decision: Any) -> str | None:
    raw = getattr(decision, "rank", None)
    if raw in {"largest", "smallest", "newest", "oldest"}:
        return cast(str, raw)
    return None


def _candidate_matches_tokens(item: SurfaceItemView, target_tokens: list[str]) -> bool:
    if not target_tokens:
        return False
    item_text = _surface_item_text(item)
    item_tokens = set(_tokens(item_text))
    if all(token in item_tokens or token in item_text for token in target_tokens):
        return True
    # Bounded typo-tolerance for visible names only; this never chooses between multiple matches.
    return all(
        any(
            len(token) >= 4
            and len(candidate) >= 4
            and SequenceMatcher(None, token, candidate).ratio() >= 0.78
            for candidate in item_tokens
        )
        for token in target_tokens
    )


def _ranked_matches(candidates: list[QuerySurfaceMatch], rank: str | None) -> list[QuerySurfaceMatch]:
    if not candidates or rank is None:
        return []
    if rank in {"largest", "smallest"}:
        valued = [match for match in candidates if match.item.amount is not None]
        if not valued:
            return []
        selected = (
            max(valued, key=_match_abs_amount)
            if rank == "largest"
            else min(valued, key=_match_abs_amount)
        )
        return [selected]
    dated = [
        (match, _parse_date((match.item.metadata or {}).get("date") if isinstance(match.item.metadata, dict) else None))
        for match in candidates
    ]
    selected = max(dated, key=lambda pair: pair[1])[0] if rank == "newest" else min(dated, key=lambda pair: pair[1])[0]
    return [selected]


def _match_abs_amount(match: QuerySurfaceMatch) -> float:
    return abs(float(match.item.amount or 0))


def _matches_from_items(
    *,
    items: list[SurfaceItemView],
    query_result: QueryResult | None,
    decision: Any,
    text: str,
    frame_id: str | None = None,
) -> tuple[list[QuerySurfaceMatch], str | None]:
    target_index = _target_index(decision)
    if target_index is not None:
        if 0 <= target_index < len(items):
            item = items[target_index]
            query_item = _query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item)
            return [QuerySurfaceMatch(item=item, query_item=query_item, index=target_index, frame_id=frame_id)], None
        return [], f"I don't see item {target_index + 1} in the results I showed."

    candidates = [
        QuerySurfaceMatch(
            item=item,
            query_item=_query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item),
            index=idx,
            frame_id=frame_id,
        )
        for idx, item in enumerate(items)
    ]

    ranked = _ranked_matches(candidates, _rank(decision))
    if ranked:
        return ranked, None
    return [], None


def resolve_visible_query_target(
    *,
    surface_view: SurfaceView | None,
    query_result: QueryResult | None,
    decision: Any,
    text: str,
) -> tuple[list[QuerySurfaceMatch], str | None]:
    """Resolve a typed query decision against the current visible surface."""
    if surface_view is None or not surface_view.items:
        return [], None

    items = surface_view.items
    indexed_or_ranked, indexed_miss = _matches_from_items(
        items=items,
        query_result=query_result,
        decision=decision,
        text=text,
    )
    if indexed_or_ranked or indexed_miss:
        return indexed_or_ranked, indexed_miss

    amount_refs = set()
    target_amount = getattr(decision, "target_amount", None)
    if isinstance(target_amount, (int, float)):
        amount_refs.add(float(target_amount))
    amount_refs.update(_amount_reference_values(text))
    if amount_refs:
        matches = [
            QuerySurfaceMatch(
                item=item,
                query_item=_query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item),
                index=idx,
            )
            for idx, item in enumerate(items)
            if _surface_matches_amount(item, amount_refs)
        ]
        if matches:
            return matches, None
        amounts = ", ".join(_format_naira(amount) for amount in sorted(amount_refs))
        return [], f"I don't see {amounts} in the transactions I showed."

    target_text = str(getattr(decision, "target_text", "") or "").strip()
    target_tokens = _tokens(target_text)
    if target_tokens:
        matches = [
            QuerySurfaceMatch(
                item=item,
                query_item=_query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item),
                index=idx,
            )
            for idx, item in enumerate(items)
            if _candidate_matches_tokens(item, target_tokens)
        ]
        if matches:
            return matches, None
        label = " ".join(target_tokens).title()
        return [], f"I don't see {label} in the transactions I showed."

    filters = getattr(decision, "filters", None)
    if isinstance(filters, Filters):
        matches = [
            QuerySurfaceMatch(
                item=item,
                query_item=_query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item),
                index=idx,
            )
            for idx, item in enumerate(items)
            if _filter_matches(item, filters)
        ]
        if matches:
            return matches, None

    if len(items) == 1:
        item = items[0]
        return [
            QuerySurfaceMatch(
                item=item,
                query_item=_query_item_for_surface_item(query_result, item) or _query_item_from_surface_item(item),
                index=0,
            )
        ], None
    return [], None


def _frame_candidates(query_frames: list[QueryFrame] | None, decision: Any) -> list[tuple[QueryFrame, list[SurfaceItemView]]]:
    if not query_frames:
        return []
    referenced_ids = getattr(decision, "referenced_frame_ids", None)
    if isinstance(referenced_ids, list) and referenced_ids:
        selected = [frame for frame in query_frames if frame.frame_id in referenced_ids]
    else:
        selected = list(reversed(query_frames))
    candidates: list[tuple[QueryFrame, list[SurfaceItemView]]] = []
    for frame in selected:
        items = [
            item
            for snapshot in frame.visible_items
            if isinstance(snapshot, dict)
            for item in [_surface_item_from_snapshot(snapshot)]
            if item is not None
        ]
        if items:
            candidates.append((frame, items))
    return candidates


def _references_prior_context(text: str, decision: Any) -> bool:
    referenced_ids = getattr(decision, "referenced_frame_ids", None)
    if isinstance(referenced_ids, list) and referenced_ids:
        return True
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in (
            "previous",
            "prior",
            "earlier",
            "before",
            "old page",
            "last page",
            "that page",
            "previous page",
        )
    )


def resolve_query_target(
    *,
    surface_view: SurfaceView | None,
    query_result: QueryResult | None,
    query_frames: list[QueryFrame] | None,
    decision: Any,
    text: str,
) -> tuple[list[QuerySurfaceMatch], str | None]:
    """Resolve a query target against the current surface, then recent query frames."""
    current_matches, current_miss = resolve_visible_query_target(
        surface_view=surface_view,
        query_result=query_result,
        decision=decision,
        text=text,
    )
    if current_matches:
        return current_matches, None
    # Current detail surfaces cannot satisfy list indexes; use the latest list frame instead.
    target_index = _target_index(decision)
    has_target = (
        target_index is not None
        or getattr(decision, "rank", None) is not None
        or bool(str(getattr(decision, "target_text", "") or "").strip())
        or bool(_amount_reference_values(text))
    )
    current_is_detail = surface_view is not None and len(surface_view.items) == 1
    should_try_frames = has_target and (current_is_detail or _references_prior_context(text, decision))
    if not should_try_frames:
        return [], current_miss

    frame_misses: list[str] = []
    for frame, frame_items in _frame_candidates(query_frames, decision):
        matches, miss = resolve_visible_query_target(
            surface_view=SurfaceView(mode=frame.surface_type or SurfaceViewMode.TRANSACTION_LIST, items=frame_items),
            query_result=None,
            decision=decision,
            text=text,
        )
        if matches:
            return [
                QuerySurfaceMatch(
                    item=match.item,
                    query_item=match.query_item or _query_item_from_surface_item(match.item),
                    index=match.index,
                    frame_id=frame.frame_id,
                )
                for match in matches
            ], None
        if miss:
            frame_misses.append(miss)
    return [], current_miss or (frame_misses[0] if frame_misses else None)


def build_query_conversation_updates(
    *,
    surface_view: SurfaceView | None,
    query_result: QueryResult | None,
    decision: Any,
    text: str,
    query_frames: list[QueryFrame] | None = None,
) -> dict[str, Any] | None:
    """Build grounded active-query updates for visible item follow-ups."""
    if getattr(decision, "decision", None) != "continuation":
        return None
    continuation_type = getattr(decision, "continuation_type", None)
    has_target_reference = any(
        (
            getattr(decision, "target_index", None) is not None,
            getattr(decision, "target_amount", None) is not None,
            bool(str(getattr(decision, "target_text", "") or "").strip()),
            getattr(decision, "requested_field", None) is not None,
            getattr(decision, "fact_field", None) is not None,
            getattr(decision, "rank", None) is not None,
            bool(_amount_reference_values(text)),
        )
    )
    if continuation_type != "drill_down" and not has_target_reference:
        return None

    matches, miss_response = resolve_query_target(
        surface_view=surface_view,
        query_result=query_result,
        query_frames=query_frames,
        decision=decision,
        text=text,
    )
    if miss_response:
        logger.info(
            "query_conversation_grounding",
            outcome="miss",
            continuation_type=continuation_type,
            requested_field=getattr(decision, "requested_field", None) or getattr(decision, "fact_field", None),
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": miss_response,
            "session_active": True,
            "flow_state": "complete",
        }
    if len(matches) > 1:
        labels = "\n".join(f"{idx}. {match.item.label}" for idx, match in enumerate(matches[:5], 1))
        logger.info(
            "query_conversation_grounding",
            outcome="ambiguous",
            continuation_type=continuation_type,
            match_count=len(matches),
            requested_field=getattr(decision, "requested_field", None) or getattr(decision, "fact_field", None),
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": f"I found multiple matching transactions. Which one did you mean?\n\n{labels}",
            "session_active": True,
            "flow_state": "parsing",
            "pending_clarification": None,
        }
    if not matches:
        logger.info(
            "query_conversation_grounding",
            outcome="fell_through",
            continuation_type=continuation_type,
            requested_field=getattr(decision, "requested_field", None) or getattr(decision, "fact_field", None),
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        return None

    match = matches[0]
    payload: SelectionPayload = match.item.payload
    if payload.selection_kind == "group_bucket" or bool(payload.filters_patch) or payload.time_patch is not None:
        return None
    action = getattr(decision, "drill_down_action", None) or "view_details"
    field = _resolve_field(decision)
    if field is not None:
        action = "answer_fact"

    logger.info(
        "query_conversation_grounding",
        outcome="selected",
        continuation_type=continuation_type,
        action=action,
        requested_field=field,
        selected_from_frame=match.frame_id is not None,
        match_index=match.index,
        has_current_surface=surface_view is not None,
        frame_count=len(query_frames or []),
    )

    return {
        "flow_state": "executing",
        "continuation_type": "drill_down",
        "continuation_delta_type": getattr(decision, "delta_type", None),
        "resolver_message": None,
        "selected_payload": payload,
        "selected_item_index": match.index,
        "selected_item_id": match.item.id,
        **({"selected_query_item": match.query_item} if match.query_item is not None else {}),
        **({"selected_frame_id": match.frame_id} if match.frame_id is not None else {}),
        "drill_down_action": action,
        **({"fact_field": "recipient" if field == "counterparty" else field} if field else {}),
    }


__all__ = [
    "QuerySurfaceMatch",
    "build_query_conversation_updates",
    "resolve_query_target",
    "resolve_visible_query_target",
]
