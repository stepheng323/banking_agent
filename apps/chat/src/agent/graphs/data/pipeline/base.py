from abc import ABC, abstractmethod
from typing import Any

from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PipelineStep(ABC):
    """Abstract base class for data pipeline steps."""

    @abstractmethod
    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        """Execute step logic. Return TransactionResult to halt pipeline."""
        pass


class DataPipeline:
    """Executes a sequence of steps for Data purchase."""

    def __init__(self, steps: list[PipelineStep]):
        self.steps = steps

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult:
        """Run the pipeline steps sequentially."""

        current_payload = payload
        locale = context.language

        for step in self.steps:
            try:
                result = await step.run(current_payload, context, gates, worker_context)

                if result:
                    # Pipeline halted (Needs Input, Confirmation, Auth, or Failed/Done)
                    return result

                # If step returns None, it means "continue to next step"
                # Typically step modifies payload in place or returns nothing
                # if pure side-effect/validation pass.
                # But our standard V3 worker pattern often implies payload
                # updates via reference or discrete patch return.
                # Here we assume steps modify payload if needed or we'd need a different contract.
                # However, looking at TransferPipeline, steps usually return TransactionResult if they need to stop.

            except Exception as e:
                logger.error(f"step_{step.__class__.__name__}_failed", error=str(e), exc_info=True)
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("data.error.system_processing", locale),
                )

        # Fallback if no step halts (shouldn't happen for complete flow)
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("data.error.pipeline_no_result", locale),
        )
