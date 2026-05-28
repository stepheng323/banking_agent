"""Data pipeline construction."""

from apps.chat.src.agent.workers.data.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.workers.data.nodes.execution import ExecutionStep
from apps.chat.src.agent.workers.data.nodes.extraction import ExtractionStep
from apps.chat.src.agent.workers.data.nodes.plan_selection import DataPlanQueryStep, DataPlanSelectionStep
from apps.chat.src.agent.workers.data.nodes.resolution import ResolutionStep
from apps.chat.src.agent.workers.data.nodes.security import AuthorizationStep
from apps.chat.src.agent.workers.data.nodes.selection import SourceSelectionStep
from apps.chat.src.agent.workers.data.nodes.validation import ValidationStep
from apps.chat.src.agent.workers.data.pipeline.base import DataPipeline, PipelineStep
from apps.chat.src.agent.workers.data.scheduling import DataScheduleCompleteStep, DataScheduleRequirementsStep


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
