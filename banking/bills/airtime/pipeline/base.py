"""Airtime Pipeline Base."""

from abc import ABC, abstractmethod
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from banking.bills.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)


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
                data = AirtimePayload(**{**data.model_dump(), **result.patch})
                accumulated_patch.update(result.patch)

            if result.outcome != TransactionOutcome.OK:
                if accumulated_patch:
                    final_patch = result.patch or {}
                    result.patch = {**accumulated_patch, **final_patch}
                    return result
                return result

        if accumulated_patch:
            # Ensure the final result includes the full accumulated patch
            result.patch = accumulated_patch
            return result

        return result
