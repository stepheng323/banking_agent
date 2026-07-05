"""Adapters from typed presentation surfaces into orchestrator context frames."""

from __future__ import annotations

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from banking.transactions.query.contracts import FocusedReferent, SelectionPayload, SurfaceView, SurfaceViewMode

_SENSITIVE_DATA_FRAGMENTS = ("pin", "otp", "password", "token", "secret", "auth")


def _safe_surface_data(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        key_text = str(key)
        if any(fragment in key_text.lower() for fragment in _SENSITIVE_DATA_FRAGMENTS):
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key_text] = value
        elif isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) for item in value[:10]):
            safe[key_text] = value[:10]
        elif isinstance(value, dict):
            nested = _safe_surface_data(value)
            if nested:
                safe[key_text] = nested
    return safe


def _surface_frame_type(surface_view: SurfaceView) -> ContextFrameType | None:
    if surface_view.mode == SurfaceViewMode.CLARIFICATION:
        return None
    if surface_view.mode == SurfaceViewMode.DIRECT_ANSWER:
        if (
            surface_view.context.get("focus_type") == "summary_scope"
            or surface_view.context.get("type") == "summary_scope"
        ):
            return ContextFrameType.GENERIC
        if len(surface_view.items) == 1:
            return ContextFrameType.TRANSACTION_DETAIL
        return ContextFrameType.GENERIC
    if surface_view.mode in {SurfaceViewMode.TRANSACTION_LIST, SurfaceViewMode.GROUPED_SUMMARY}:
        return ContextFrameType.TRANSACTION_LIST
    return ContextFrameType.GENERIC


def _entity_type_for_payload(payload: SelectionPayload | None) -> EntityType:
    if payload is None:
        return EntityType.GENERIC
    if payload.selection_kind == "transaction":
        return EntityType.TRANSACTION
    if payload.selection_kind == "beneficiary":
        return EntityType.BENEFICIARY
    if payload.selection_kind == "account":
        return EntityType.ACCOUNT
    return EntityType.GENERIC


def _focused_referent_from_payload(payload: SelectionPayload | None) -> FocusedReferent | None:
    handoff = payload.handoff_payload if payload is not None else None
    if not isinstance(handoff, dict):
        return None

    recipient_name = str(handoff.get("recipient_name") or payload.label if payload else "").strip()
    if not recipient_name:
        return None

    return FocusedReferent(
        referent_type="beneficiary",
        label=recipient_name,
        entity_id=payload.entity_id if payload else None,
        selection_payload=payload,
        recipient_name=recipient_name,
        recipient_account=str(handoff.get("recipient_account") or "").strip() or None,
        recipient_bank_name=str(handoff.get("recipient_bank_name") or "").strip() or None,
        recipient_bank_code=str(handoff.get("recipient_bank_code") or "").strip() or None,
        recipient_resolved_name=str(handoff.get("recipient_resolved_name") or recipient_name).strip() or None,
    )


def build_context_frame_from_surface_view(
    surface_view: SurfaceView,
    *,
    source: str,
    source_message_id: str | None = None,
    ttl_seconds: int = 600,
) -> ContextFrame | None:
    """Build a safe context frame from a typed user-visible surface."""
    frame_type = _surface_frame_type(surface_view)
    if frame_type is None or not surface_view.items:
        return None

    context_data = _safe_surface_data(surface_view.context if isinstance(surface_view.context, dict) else {})
    entities: list[ContextEntity] = []
    for idx, item in enumerate(surface_view.items, 1):
        payload = item.payload if isinstance(item.payload, SelectionPayload) else None
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        label = str(item.label or metadata.get("description") or f"Result {idx}").strip()
        data = _safe_surface_data(
            {
                "id": item.id,
                "label": label,
                "amount": item.amount,
                "count": item.count,
                "surface_mode": surface_view.mode.value,
                "lead_text": surface_view.lead_text,
                **context_data,
                **metadata,
            }
        )
        entities.append(
            ContextEntity(
                entity_type=_entity_type_for_payload(payload),
                entity_id=str(item.id or payload.entity_id or idx) if (item.id or payload) else str(idx),
                label=label,
                data=data,
                selection_payload=payload,
                focused_referent=_focused_referent_from_payload(payload),
            )
        )

    return ContextFrame(
        frame_id=f"{source}_surface_{int(time.time())}",
        frame_type=frame_type,
        items=entities,
        focus_index=0,
        created_at_ts=int(time.time()),
        source_message_id=source_message_id,
        ttl_seconds=ttl_seconds,
        metadata={"source": source, "surface_mode": surface_view.mode.value},
    )


__all__ = ["build_context_frame_from_surface_view"]
