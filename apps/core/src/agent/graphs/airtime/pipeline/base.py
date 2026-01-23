"""Airtime Pipeline Base."""

from abc import ABC, abstractmethod
from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


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

        for step in self.steps:
            result = await step.execute(data, context, gates, worker_context)
            if result.outcome != TransactionOutcome.OK:
                return result

        return TransactionResult(outcome=TransactionOutcome.OK)
