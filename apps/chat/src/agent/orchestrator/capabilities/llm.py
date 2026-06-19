from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_semantic import (
    unsupported_boundary_turn_messages,
    unsupported_capability_semantic_messages,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_model_wiring import with_structured_output
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_observability import invoke_structured_prompt
from shared.observability.llm import build_llm_runnable_config
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class CapabilityClassifierLLM:
    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm
        self.structured_unsupported_capability = with_structured_output(
            llm,
            UnsupportedCapabilitySemanticOutput,
        )
        self.structured_unsupported_boundary_turn = with_structured_output(
            llm,
            UnsupportedBoundaryTurnOutput,
        )

    async def classify_unsupported_capability(
        self,
        text: str,
        *,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "direct_path",
    ) -> UnsupportedCapabilitySemanticOutput:
        """Bounded semantic classifier for unsupported capability boundaries."""
        messages = unsupported_capability_semantic_messages(text=text, locale=locale, context=context)
        try:
            return await invoke_structured_prompt(
                self.structured_unsupported_capability,
                UnsupportedCapabilitySemanticOutput,
                system_prompt=messages[0]["content"],
                user_prompt=messages[1]["content"],
                logger=logger,
                event_name="unsupported_capability_semantic_llm_call",
                model_llm=self.llm,
                path_label=path_label,
                latency_span="unsupported_capability_semantic_llm",
                log_fields={
                    "context_chars": len(context),
                    "context_mode": "compact" if context == "None" else "full",
                },
                config=build_llm_runnable_config(
                    role="semantic_router",
                    path_label=path_label,
                    task_domain="unsupported_capability",
                    locale=locale,
                ),
                prompt_cache_key="unsupported_capability:semantic",
            )
        except Exception:
            return UnsupportedCapabilitySemanticOutput(
                action="unclear",
                capability_key=None,
                confidence=0.0,
                reason="semantic_classifier_failed",
            )

    async def classify_unsupported_boundary_turn(
        self,
        text: str,
        *,
        boundary_key: str,
        boundary_label: str,
        followup_count: int = 0,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "direct_path",
    ) -> UnsupportedBoundaryTurnOutput:
        """Bounded semantic classifier for turns after an unsupported capability refusal."""
        messages = unsupported_boundary_turn_messages(
            text=text,
            boundary_key=boundary_key,
            boundary_label=boundary_label,
            followup_count=followup_count,
            locale=locale,
            context=context,
        )
        try:
            return await invoke_structured_prompt(
                self.structured_unsupported_boundary_turn,
                UnsupportedBoundaryTurnOutput,
                system_prompt=messages[0]["content"],
                user_prompt=messages[1]["content"],
                logger=logger,
                event_name="unsupported_boundary_turn_llm_call",
                model_llm=self.llm,
                path_label=path_label,
                latency_span="unsupported_boundary_turn_llm",
                log_fields={
                    "boundary_key": boundary_key,
                    "context_chars": len(context),
                    "context_mode": "compact" if context == "None" else "full",
                },
                config=build_llm_runnable_config(
                    role="semantic_router",
                    path_label=path_label,
                    task_domain="unsupported_capability",
                    locale=locale,
                    extra_metadata={"boundary_key": boundary_key},
                ),
                prompt_cache_key=f"unsupported_boundary:{boundary_key}",
            )
        except Exception:
            return UnsupportedBoundaryTurnOutput(
                action="unclear",
                capability_key=None,
                confidence=0.0,
                reason="boundary_turn_classifier_failed",
            )
