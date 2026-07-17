"""Read-only adapter from mature support threads to shared set telemetry."""

from __future__ import annotations

from hashlib import sha256
from time import time

from banking.support.models import SupportContext, SupportReferenceCandidate
from shared.types.conversation_sets import ConversationSetState, EntitySelectionRef


def _candidate_ref(candidate: SupportReferenceCandidate, *, frame_id: str) -> EntitySelectionRef:
    label = candidate.recipient_label or candidate.task_type or f"Item {candidate.ordinal}"
    version = sha256(
        f"{candidate.transaction_id}:{candidate.final_status}:{candidate.receipt_allowed}".encode()
    ).hexdigest()[:20]
    return EntitySelectionRef(
        entity_type="support_reference",
        entity_id=candidate.transaction_id,
        frame_id=frame_id,
        display_label=label,
        version_token=version,
    )


def support_conversation_set(context: SupportContext) -> ConversationSetState | None:
    """Expose support focus/history without replacing its deterministic selectors."""
    thread = context.receipt_thread_state
    pending = context.pending_reference
    if thread is not None and (thread.expires_at_ts is None or thread.expires_at_ts > time()):
        frame_id = f"support-receipt:{thread.async_group_id}"
        refs = [_candidate_ref(candidate, frame_id=frame_id) for candidate in thread.candidates[:20]]
        by_id = {ref.entity_id: ref for ref in refs}
        last = [by_id[value] for value in thread.last_selector_result_ids if value in by_id]
        mentioned = [by_id[value] for value in thread.served_transaction_ids if value in by_id]
        return ConversationSetState(
            domain="support",
            focused_ref=last[0] if len(last) == 1 else None,
            mentioned_refs=mentioned or refs,
            last_result_refs=last or refs,
            operation="receipt_selection",
        )
    if pending is not None and (pending.expires_at_ts is None or pending.expires_at_ts > time()):
        refs = [_candidate_ref(candidate, frame_id="support-pending") for candidate in pending.candidates[:20]]
        return ConversationSetState(
            domain="support",
            focused_ref=refs[0] if len(refs) == 1 else None,
            mentioned_refs=refs,
            last_result_refs=refs,
            operation=pending.intent,
        )
    return None


def support_set_telemetry(context: SupportContext) -> dict[str, int | str | bool]:
    """Return privacy-safe common telemetry for support selection state."""
    state = support_conversation_set(context)
    return {
        "domain": "support",
        "has_focus": bool(state and state.focused_ref),
        "mentioned_count": len(state.mentioned_refs) if state else 0,
        "last_result_count": len(state.last_result_refs) if state else 0,
    }


__all__ = ["support_conversation_set", "support_set_telemetry"]
