import time

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.context.referents.frame_memory import remember_referents_from_frame
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CONTEXT_FRAME_ITEM_PREVIEW_LIMIT = 5
CONTEXT_FRAME_LABEL_MAX_CHARS = 64
CONTEXT_FRAME_DETAILS_MAX_CHARS = 48
CONTEXT_FRAME_SUMMARY_MAX_CHARS = 1200


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 16:
        return value[:max_chars]
    return value[: max_chars - 15].rstrip() + " ...[truncated]"


class ContextFrameManager:
    """Manages short-term context frames for the Orchestrator."""

    def __init__(self, max_frames: int = 5):
        self.max_frames = max_frames

    def push_frame(self, state: OrchestratorState, frame: ContextFrame) -> OrchestratorState:
        """Push a new context frame to the state."""
        # 1. Prune expired
        now = int(time.time())
        active_frames = [f for f in state.context_frames if (f.created_at_ts + f.ttl_seconds) > now]

        # 2. Append new frame
        active_frames.append(frame)

        # 3. Cap length (keep most recent)
        if len(active_frames) > self.max_frames:
            active_frames = active_frames[-self.max_frames :]

        state.context_frames = active_frames
        remember_referents_from_frame(state, frame)
        logger.info("context_frame_pushed", type=frame.frame_type, frame_id=frame.frame_id)
        return state

    @staticmethod
    def _is_active_frame(frame: ContextFrame, now: int) -> bool:
        return (frame.created_at_ts + frame.ttl_seconds) > now

    def _latest_active_frame(
        self,
        state: OrchestratorState,
        *,
        frame_type: ContextFrameType | None = None,
    ) -> ContextFrame | None:
        now = int(time.time())
        for frame in reversed(state.context_frames):
            if frame_type is not None and frame.frame_type != frame_type:
                continue
            if not self._is_active_frame(frame, now):
                continue
            if frame.items:
                return frame
        return None

    def latest_active_frame(self, state: OrchestratorState) -> ContextFrame | None:
        """Return the latest active frame with entries, regardless of frame type."""
        return self._latest_active_frame(state)

    def latest_beneficiary_frame(self, state: OrchestratorState) -> ContextFrame | None:
        """Return the latest active beneficiary-list frame with entries."""
        return self._latest_active_frame(state, frame_type=ContextFrameType.BENEFICIARY_LIST)

    def build_llm_summary(self, state: OrchestratorState) -> str:
        """Generate compact summary of active context for LLM."""
        if not state.context_frames:
            return ""

        summary_parts = ["Active Context (Most recent last):"]

        # Prune expired on read (lazy cleanup)
        now = int(time.time())
        valid_frames = [f for f in state.context_frames if (f.created_at_ts + f.ttl_seconds) > now]

        for frame in valid_frames:
            resume_item = next((item for item in frame.items if item.data.get("resume_prompt") is True), None)
            if resume_item:
                intent_raw = resume_item.data.get("intent", "transaction")
                intent = intent_raw if isinstance(intent_raw, str) and intent_raw else "transaction"
                summary_parts.append(f"- resumption: Asked to resume {intent}")
                continue
            items_str = ""
            preview_items = frame.items[:CONTEXT_FRAME_ITEM_PREVIEW_LIMIT]
            overflow_count = len(frame.items) - len(preview_items)
            if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
                # [1] Mum (GTB) [2] Dad (Access)
                items = []
                for idx, item in enumerate(preview_items, 1):
                    details = item.data.get("bank", "") or item.data.get("account", "")
                    label = _clip_text(item.label or "Unknown", CONTEXT_FRAME_LABEL_MAX_CHARS)
                    detail_text = _clip_text(str(details), CONTEXT_FRAME_DETAILS_MAX_CHARS)
                    items.append(f"[{idx}] {label} ({detail_text})")
                items_str = ", ".join(items)

            elif frame.frame_type == ContextFrameType.TRANSACTION_LIST:
                items = []
                for idx, item in enumerate(preview_items, 1):
                    amt = item.data.get("amount", "")
                    label = _clip_text(item.label or "Unknown", CONTEXT_FRAME_LABEL_MAX_CHARS)
                    amount_text = _clip_text(str(amt), CONTEXT_FRAME_DETAILS_MAX_CHARS)
                    items.append(f"[{idx}] {label} ({amount_text})")
                items_str = ", ".join(items)

            elif frame.frame_type == ContextFrameType.SCHEDULE_LIST:
                items = []
                for idx, item in enumerate(preview_items, 1):
                    target = item.data.get("target", "")
                    label = _clip_text(item.label or "Scheduled transaction", CONTEXT_FRAME_LABEL_MAX_CHARS)
                    target_text = _clip_text(str(target), CONTEXT_FRAME_DETAILS_MAX_CHARS)
                    items.append(f"[{idx}] {label} ({target_text})")
                items_str = ", ".join(items)

            elif frame.frame_type == ContextFrameType.DATA_PLAN_LIST:
                items = []
                for idx, item in enumerate(preview_items, 1):
                    amount = item.data.get("amount", "")
                    label = _clip_text(item.label or "Data plan", CONTEXT_FRAME_LABEL_MAX_CHARS)
                    amount_text = _clip_text(str(amount), CONTEXT_FRAME_DETAILS_MAX_CHARS)
                    items.append(f"[{idx}] {label} ({amount_text})")
                items_str = ", ".join(items)

            elif frame.frame_type == ContextFrameType.RECEIPT:
                receipt_item = frame.items[0] if frame.items else None
                if receipt_item:
                    label = _clip_text(receipt_item.label or "Receipt", CONTEXT_FRAME_LABEL_MAX_CHARS)
                    amount_text = _clip_text(str(receipt_item.data.get("amount", "")), CONTEXT_FRAME_DETAILS_MAX_CHARS)
                    items_str = f"{label} - {amount_text}"

            else:
                items = [
                    f"[{idx}] {_clip_text(item.label or 'Unknown', CONTEXT_FRAME_LABEL_MAX_CHARS)}"
                    for idx, item in enumerate(preview_items, 1)
                ]
                items_str = ", ".join(items)

            if overflow_count > 0:
                items_str = (
                    f"{items_str}, ... (+{overflow_count} more)" if items_str else f"... (+{overflow_count} more)"
                )
            summary_parts.append(f"- {frame.frame_type.value}: {items_str}")

        return _clip_text("\n".join(summary_parts), CONTEXT_FRAME_SUMMARY_MAX_CHARS)

    def resolve_reference(self, state: OrchestratorState, ref: dict) -> ContextEntity | None:
        """Resolve a reference dictionary to a specific entity."""
        if not state.context_frames:
            return None

        selector = ref.get("selector")

        last_list_frame = self._latest_active_frame(state)

        if selector == "index" and last_list_frame:
            try:
                idx = int(ref.get("index", 1)) - 1  # 1-based to 0-based
                if 0 <= idx < len(last_list_frame.items):
                    return last_list_frame.items[idx]
            except ValueError:
                pass

        elif selector == "previous":
            if last_list_frame and last_list_frame.items:
                if 0 <= last_list_frame.focus_index < len(last_list_frame.items):
                    return last_list_frame.items[last_list_frame.focus_index]
                return last_list_frame.items[-1]

        return None
