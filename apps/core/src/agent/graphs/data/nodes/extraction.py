from typing import Any

from apps.core.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(PipelineStep):
    """Extraction Step: Parse user message into DataPayload."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if not self.user_message:
            return None

        if payload.skip_extraction:
            payload.skip_extraction = False
            return None

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" to an account selection prompt, map it directly.
        numeric_patch = try_extract_numeric_index(self.user_message, "data")
        if numeric_patch:
            payload.source_account_index = numeric_patch["source_account_index"]
            payload.stage = "extracted"
            return None

        extractor = worker_context.extractor
        if not extractor:
            logger.info("data_extraction_skipped", reason="extractor_unavailable")
            return None
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
