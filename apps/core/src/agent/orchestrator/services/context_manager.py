import time

from apps.core.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorContextManager:
    """Manages short-term context frames for the Orchestrator."""

    def __init__(self, max_frames: int = 5):
        self.max_frames = max_frames

    def push_frame(self, state: OrchestratorState, frame: ContextFrame) -> OrchestratorState:
        """Push a new context frame to the state."""
        # 1. Prune expired
        now = int(time.time())
        active_frames = [
            f for f in state.context_frames if (f.created_at_ts + f.ttl_seconds) > now
        ]

        # 2. Append new frame
        active_frames.append(frame)

        # 3. Cap length (keep most recent)
        if len(active_frames) > self.max_frames:
            active_frames = active_frames[-self.max_frames :]

        state.context_frames = active_frames
        logger.info("context_frame_pushed", type=frame.frame_type, frame_id=frame.frame_id)
        return state

    def build_llm_summary(self, state: OrchestratorState) -> str:
        """Generate compact summary of active context for LLM."""
        if not state.context_frames:
            return ""

        summary_parts = ["Active Context (Most recent last):"]
        
        # Prune expired on read (lazy cleanup)
        now = int(time.time())
        valid_frames = [f for f in state.context_frames if (f.created_at_ts + f.ttl_seconds) > now]
        
        for i, frame in enumerate(valid_frames):
            items_str = ""
            if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
                # [1] Mum (GTB) [2] Dad (Access)
                items = []
                for idx, item in enumerate(frame.items, 1):
                    details = item.data.get("bank", "") or item.data.get("account", "")
                    items.append(f"[{idx}] {item.label} ({details})")
                items_str = ", ".join(items)
            
            elif frame.frame_type == ContextFrameType.TRANSACTION_LIST:
                items = []
                for idx, item in enumerate(frame.items, 1):
                    amt = item.data.get("amount", "")
                    items.append(f"[{idx}] {item.label} ({amt})")
                items_str = ", ".join(items)
                
            elif frame.frame_type == ContextFrameType.RECEIPT:
                item = frame.items[0] if frame.items else None
                if item:
                    items_str = f"{item.label} - {item.data.get('amount','')}"

            else:
                items = [f"[{idx}] {item.label}" for idx, item in enumerate(frame.items, 1)]
                items_str = ", ".join(items)

            summary_parts.append(f"- {frame.frame_type.value}: {items_str}")

        return "\n".join(summary_parts)

    def resolve_reference(self, state: OrchestratorState, ref: dict) -> ContextEntity | None:
        """Resolve a reference dictionary to a specific entity."""
        if not state.context_frames:
            return None
            
        selector = ref.get("selector")
        
        # Get last valid list frame for index lookups
        last_list_frame = None
        for f in reversed(state.context_frames):
            if f.items and len(f.items) > 0:
                last_list_frame = f
                break
                
        if selector == "index" and last_list_frame:
            try:
                idx = int(ref.get("index", 1)) - 1  # 1-based to 0-based
                if 0 <= idx < len(last_list_frame.items):
                    return last_list_frame.items[idx]
            except ValueError:
                pass
                
        elif selector == "previous":
            # Just return the very last entity shown
            if last_list_frame and last_list_frame.items:
                return last_list_frame.items[-1] # or focus index if tracked

        return None
