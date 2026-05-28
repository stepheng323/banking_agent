from dataclasses import dataclass

from shared.types.planner import PendingActionEditDecision

PENDING_ACTION_EDIT_MIN_CONFIDENCE = 0.55
PENDING_ACTION_EDIT_TASK_TYPES = {"transfer", "airtime", "data"}


@dataclass(frozen=True, slots=True)
class PendingActionEditResolution:
    decision: PendingActionEditDecision

    @property
    def operation(self) -> str:
        return self.decision.operation


__all__ = [
    "PENDING_ACTION_EDIT_MIN_CONFIDENCE",
    "PENDING_ACTION_EDIT_TASK_TYPES",
    "PendingActionEditResolution",
]
