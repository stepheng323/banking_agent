"""Transfer pipeline construction."""

from typing import Any

from banking.transfers.nodes.confirmation import ConfirmationStep
from banking.transfers.nodes.execution import ExecutionStep
from banking.transfers.nodes.extraction import ExtractionStep
from banking.transfers.nodes.funding import FundingStep
from banking.transfers.nodes.payout_preparation import PayoutPreparationStep
from banking.transfers.nodes.resolution import ResolutionStep
from banking.transfers.nodes.security import AuthorizationStep
from banking.transfers.nodes.selection import SourceSelectionStep
from banking.transfers.nodes.validation import ValidationStep
from banking.transfers.pipeline.base import TransferPipeline
from banking.transfers.scheduling import ScheduleRequirementsStep


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
