"""Airtime Pipeline Base."""

from abc import ABC, abstractmethod
from typing import Any

from apps.chat.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class AirtimeStep(ABC):
    """Abstract base class for airtime pipeline steps."""

    @abstractmethod
    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        """Execute the step."""
        pass


class AirtimePipeline:
    """Pipelines sequential steps for airtime processing."""

    def __init__(self, steps: list[AirtimeStep]):
        self.steps = steps

    async def run(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        """Run all steps in sequence."""

        accumulated_patch = {}

        for step in self.steps:
            result = await step.execute(data, context, gates, worker_context)

            if result.patch:
                data = data.model_copy(update=result.patch)
                accumulated_patch.update(result.patch)

            if result.outcome != TransactionOutcome.OK:
                if accumulated_patch:
                    final_patch = result.patch or {}
                    final_patch = {**accumulated_patch, **final_patch}

                    return TransactionResult(
                        outcome=result.outcome,
                        patch=final_patch,
                        error=result.error,
                        prompt=result.prompt,
                        required_fields=result.required_fields,
                        details=result.details,
                        confirmation_summary=result.confirmation_summary,
                    )
                return result

        if accumulated_patch:
            # Ensure the final result includes the full accumulated patch
            result.patch = accumulated_patch
            return result

        return result
