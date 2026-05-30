from abc import ABC, abstractmethod
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PipelineStep(ABC):
    """Abstract base class for data pipeline steps."""

    @abstractmethod
    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult:
        """Execute step logic. OK continues; any other outcome halts."""
        pass


def continue_pipeline(payload: DataPayload | None = None) -> TransactionResult:
    del payload
    return TransactionResult(outcome=TransactionOutcome.OK, patch={})


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
        accumulated_patch: dict[str, Any] = {}
        last_result: TransactionResult | None = None

        for step in self.steps:
            try:
                result = await step.run(current_payload, context, gates, worker_context)

                if result.patch:
                    current_payload = DataPayload(**{**current_payload.model_dump(), **result.patch})
                    accumulated_patch.update(result.patch)

                if result.outcome != TransactionOutcome.OK:
                    if accumulated_patch:
                        result.patch = {**accumulated_patch, **(result.patch or {})}
                    return result

                last_result = result

            except Exception as e:
                logger.error(f"step_{step.__class__.__name__}_failed", error=str(e), exc_info=True)
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("data.error.system_processing", locale),
                )

        if last_result:
            if accumulated_patch:
                last_result.patch = {**accumulated_patch, **(last_result.patch or {})}
            return last_result

        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("data.error.pipeline_no_result", locale),
        )
