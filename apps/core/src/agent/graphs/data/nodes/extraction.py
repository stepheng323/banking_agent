from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionResult


class ExtractionStep(PipelineStep):
    """Extraction Step: Parse user message into DataPayload."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:

        if not self.user_message:
            return None

        extractor = worker_context.extractor
        extraction_result = await extractor.extract(self.user_message)

        payload.extraction = extraction_result

        if extraction_result.entities.recipient_phone:
            payload.target_phone = extraction_result.entities.recipient_phone

        if extraction_result.entities.network:
            payload.network = extraction_result.entities.network

        # TODO: Handle 'amount' or 'budget' text to float mapping more robustly if needed
        # For now assuming simple mapping usually happens in resolution or prior

        payload.stage = "extracted"
        return None  # Continue pipeline
