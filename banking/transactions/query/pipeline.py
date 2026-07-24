"""Query pipeline definition."""

from abc import ABC, abstractmethod
from typing import Any

from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_STALE_SELECTION_KEYS = {
    "selected_item_index",
    "selected_item_id",
    "selected_payload",
    "selected_query_item",
    "selected_frame_id",
    "fact_field",
    "drill_down_action",
}


class QueryStep(ABC):
    """Abstract base class for a single step in the query pipeline."""

    @abstractmethod
    async def run(
        self,
        state: dict[str, Any],
        worker_context: Any = None,
    ) -> TransactionResult:
        """Execute the step logic."""
        pass


class QueryPipeline:
    """Execute a sequence of QuerySteps."""

    def __init__(self, steps: list[QueryStep]):
        self.steps = steps

    async def run(
        self,
        state: dict[str, Any],
        worker_context: Any = None,
    ) -> TransactionResult:
        """Run all steps in sequence."""
        last_result = None

        for step in self.steps:
            result = await step.run(state, worker_context)

            if result.outcome != TransactionOutcome.OK:
                # Stop pipeline if not OK (e.g. NEEDS_INPUT or FAILED)
                return self._finalize(result, state)

            last_result = result
            if result.patch:
                state.update(result.patch)
            if result.response is not None:
                state["response"] = result.response

        if last_result:
            return self._finalize(last_result, state)

        return self._finalize(TransactionResult(outcome=TransactionOutcome.OK, patch={}), state)

    def _finalize(self, result: TransactionResult, state: dict[str, Any]) -> TransactionResult:
        """Finalize result with accumulated state."""
        if result.patch is None:
            result.patch = {}

        explicit_patch_keys = set(result.patch)
        # Merge current state into result.patch
        result.patch.update(state)
        if "query_request" in explicit_patch_keys:
            for key in _STALE_SELECTION_KEYS:
                if key not in explicit_patch_keys:
                    result.patch.pop(key, None)

        return result
