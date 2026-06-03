"""Data pipeline construction."""

from banking.bills.data.nodes.confirmation import ConfirmationStep
from banking.bills.data.nodes.execution import ExecutionStep
from banking.bills.data.nodes.extraction import ExtractionStep
from banking.bills.data.nodes.plan_selection import DataPlanQueryStep, DataPlanSelectionStep
from banking.bills.data.nodes.resolution import ResolutionStep
from banking.bills.data.nodes.security import AuthorizationStep
from banking.bills.data.nodes.selection import SourceSelectionStep
from banking.bills.data.nodes.validation import ValidationStep
from banking.bills.data.pipeline.base import DataPipeline, PipelineStep
from banking.bills.data.scheduling import DataScheduleCompleteStep, DataScheduleRequirementsStep


def build_data_pipeline(
    user_message: str | None,
    *,
    include_execution: bool = True,
    require_schedule_fields: bool = False,
) -> DataPipeline:
    steps: list[PipelineStep] = [
        ExtractionStep(user_message),
        DataPlanSelectionStep(user_message),
        ResolutionStep(),
        DataPlanSelectionStep(user_message),
        SourceSelectionStep(),
        ValidationStep(),
    ]
    if require_schedule_fields:
        steps.append(DataScheduleRequirementsStep())
    steps.extend([ConfirmationStep(), AuthorizationStep()])
    if include_execution:
        steps.append(ExecutionStep())
    else:
        steps.append(DataScheduleCompleteStep())
    return DataPipeline(steps)


def build_data_plan_query_pipeline(user_message: str | None) -> DataPipeline:
    return DataPipeline([ExtractionStep(user_message), DataPlanQueryStep()])
