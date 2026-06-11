"""Normal message invocation runner for the orchestrator graph."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.graph.state import CompiledStateGraph

from apps.chat.src.agent.orchestrator.context.context_manager import ContextManager
from apps.chat.src.agent.orchestrator.graph.housekeeping import OrchestratorHousekeeping
from apps.chat.src.agent.orchestrator.graph.invocation_context import (
    build_graph_inputs,
    build_invocation_result,
    load_invocation_context,
    typing_visibility_delay_ms,
)
from apps.chat.src.agent.orchestrator.graph.preflight import plan_invocation_preflight
from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressTracker
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery
from apps.chat.src.agent.orchestrator.graph.progress_lifecycle import (
    start_progress_delivery,
    stop_progress_delivery,
)
from apps.chat.src.agent.orchestrator.graph.route_metrics import (
    log_latency_span,
    log_route_metrics,
    log_semantic_path_shape,
    log_turn_summary,
    record_guardrail_signal,
    resolve_path_label,
    resolve_semantic_path_shape,
)
from apps.chat.src.agent.orchestrator.graph.runtime import GraphRunnableConfig
from apps.chat.src.agent.orchestrator.graph.turn_trace import log_orchestrator_turn_trace
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from shared.observability.llm import build_llm_runnable_config
from shared.utils.logging import log_fingerprint, log_orchestrator_diagnostic


class GraphConfigFactory(Protocol):
    def __call__(
        self,
        phone_number: str,
        channel: str,
        *,
        progress_tracker: TurnProgressTracker | None = None,
    ) -> GraphRunnableConfig: ...


@dataclass(slots=True)
class GraphInvocationRunner:
    graph: CompiledStateGraph
    context_manager: ContextManager
    progress_delivery: OrchestratorProgressDelivery
    housekeeping: OrchestratorHousekeeping
    get_config: GraphConfigFactory
    error_window: deque[int]
    logger: Any
    progress_tracker_factory: Callable[..., TurnProgressTracker]

    async def run(self, context: MessageContext) -> dict[str, Any]:
        turn_start = time.perf_counter()
        phone_number = context.phone_number
        preflight = plan_invocation_preflight(context, logger=self.logger)
        path_label = preflight.path_label

        try:
            inputs = build_graph_inputs(context)

            h_start = time.perf_counter()
            loaded_context = await load_invocation_context(
                context_manager=self.context_manager,
                context=context,
                preflight=preflight,
                path_label=path_label,
            )
            h_duration = (time.perf_counter() - h_start) * 1000
            inputs["loaded_context"] = loaded_context

            progress_tracker = self.progress_tracker_factory(locale=loaded_context["language"])
            graph_config = self.get_config(
                phone_number,
                channel=context.channel,
                progress_tracker=progress_tracker,
            )
            config = graph_config.config
            thread_id = graph_config.thread_id
            turn_id = context.message_id or f"invoke-{time.monotonic_ns()}"

            trace_config = build_llm_runnable_config(
                role="orchestrator_graph",
                channel=context.channel,
                path_label=path_label,
                phone_number=phone_number,
                channel_identity=context.channel_identity,
                message_id=context.message_id,
                turn_id=turn_id,
                locale=loaded_context["language"],
                task_domain="orchestrator",
            )
            if trace_config:
                config["tags"] = list(trace_config.get("tags", []))
                config["metadata"] = dict(trace_config.get("metadata", {}))

            progress_run = start_progress_delivery(
                progress_delivery=self.progress_delivery,
                tracker=progress_tracker,
                phone_number=phone_number,
                channel=context.channel,
                channel_identity=context.channel_identity,
                inbound_message_id=context.message_id,
                thread_id=thread_id,
                turn_id=turn_id,
                enable_initial_typing=preflight.enable_initial_typing,
            )

            self.logger.info(
                "orchestrator_graph_invoke",
                phone_hash=log_fingerprint(phone_number),
                channel=context.channel,
                message_id_hash=log_fingerprint(context.message_id),
            )

            g_start = time.perf_counter()
            try:
                final_state = await self.graph.ainvoke(inputs, config=config)
            finally:
                progress_snapshot = await stop_progress_delivery(progress_run)
            g_duration = (time.perf_counter() - g_start) * 1000

            path_label = resolve_path_label(context, final_state)
            semantic_path_shape = resolve_semantic_path_shape(context, final_state, path_label)
            log_latency_span(
                self.logger,
                span="context_hydration",
                duration_ms=h_duration,
                phone_number=phone_number,
                path_label=path_label,
            )
            log_latency_span(
                self.logger,
                span="graph_execution",
                duration_ms=g_duration,
                phone_number=phone_number,
                path_label=path_label,
            )

            await self.housekeeping.run(
                thread_id=thread_id,
                state=final_state,
                phone_number=phone_number,
                path_label=path_label,
            )

            result = build_invocation_result(
                final_state=final_state,
                loaded_context=loaded_context,
                semantic_path_shape=semantic_path_shape,
            )
            log_orchestrator_diagnostic(
                self.logger,
                "orchestrator_progress_delivery_summary",
                progress_stage=progress_snapshot.stage_key,
                progress_count=progress_snapshot.progress_count,
                visible_progress_sent=progress_snapshot.progress_count > 0,
                typing_policy="consumer_owned_initial_typing",
                preflight_initial_typing_enabled=preflight.enable_initial_typing,
                typing_visibility_delay_ms=typing_visibility_delay_ms(context.channel),
            )
            total_duration = (time.perf_counter() - turn_start) * 1000
            log_latency_span(
                self.logger,
                span="orchestrator_turn_total",
                duration_ms=total_duration,
                phone_number=phone_number,
                path_label=path_label,
            )
            log_turn_summary(
                self.logger,
                final_state=final_state,
                path_label=path_label,
                total_duration_ms=total_duration,
            )
            log_orchestrator_diagnostic(
                self.logger,
                "orchestrator_path_label",
                path_label=path_label,
                phone_number=phone_number,
            )
            log_semantic_path_shape(
                self.logger,
                semantic_path_shape=semantic_path_shape,
                path_label=path_label,
                phone_number=phone_number,
            )
            log_route_metrics(
                self.logger,
                final_state=final_state,
                phone_number=phone_number,
                path_label=path_label,
                semantic_path_shape=semantic_path_shape,
                total_duration_ms=total_duration,
                progress_count=progress_snapshot.progress_count,
            )
            log_orchestrator_turn_trace(
                self.logger,
                final_state=final_state,
                path_label=path_label,
                semantic_path_shape=semantic_path_shape,
                total_duration_ms=total_duration,
                progress_count=progress_snapshot.progress_count,
            )
            record_guardrail_signal(
                self.error_window,
                self.logger,
                path_label=path_label,
                duration_ms=total_duration,
                errored=False,
            )
            return result
        except Exception:
            total_duration = (time.perf_counter() - turn_start) * 1000
            log_latency_span(
                self.logger,
                span="orchestrator_turn_total",
                duration_ms=total_duration,
                phone_number=phone_number,
                path_label=path_label,
            )
            record_guardrail_signal(
                self.error_window,
                self.logger,
                path_label=path_label,
                duration_ms=total_duration,
                errored=True,
            )
            raise


__all__ = ["GraphConfigFactory", "GraphInvocationRunner"]
