"""Query pipeline definition."""

from abc import ABC, abstractmethod
from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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

        if last_result:
            return self._finalize(last_result, state)

        return self._finalize(
            TransactionResult(outcome=TransactionOutcome.OK, patch={}), state
        )

    def _finalize(self, result: TransactionResult, state: dict[str, Any]) -> TransactionResult:
        """Finalize result with accumulated state."""
        if result.patch is None:
            result.patch = {}
        
        # Merge current state into result.patch
        result.patch.update(state)
        
        return result
