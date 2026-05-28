"""Transfer pipeline construction."""

from typing import Any

from apps.chat.src.agent.workers.transfer.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.workers.transfer.nodes.execution import ExecutionStep
from apps.chat.src.agent.workers.transfer.nodes.extraction import ExtractionStep
from apps.chat.src.agent.workers.transfer.nodes.funding import FundingStep
from apps.chat.src.agent.workers.transfer.nodes.payout_preparation import PayoutPreparationStep
from apps.chat.src.agent.workers.transfer.nodes.resolution import ResolutionStep
from apps.chat.src.agent.workers.transfer.nodes.security import AuthorizationStep
from apps.chat.src.agent.workers.transfer.nodes.selection import SourceSelectionStep
from apps.chat.src.agent.workers.transfer.nodes.validation import ValidationStep
from apps.chat.src.agent.workers.transfer.pipeline.base import TransferPipeline
from apps.chat.src.agent.workers.transfer.scheduling import ScheduleRequirementsStep


def build_transfer_pipeline(
    user_message: str | None,
    *,
    include_execution: bool = True,
    require_schedule_fields: bool = False,
) -> TransferPipeline:
    steps: list[Any] = [
        ExtractionStep(user_message),
        ResolutionStep(),
        SourceSelectionStep(),
        ValidationStep(),
        FundingStep(),
        PayoutPreparationStep(),
    ]
    if require_schedule_fields:
        steps.append(ScheduleRequirementsStep())
    steps.extend([ConfirmationStep(), AuthorizationStep()])
    if include_execution:
        steps.append(ExecutionStep())
    return TransferPipeline(steps)
