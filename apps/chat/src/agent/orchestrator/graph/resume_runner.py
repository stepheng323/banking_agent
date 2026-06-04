"""External callback resume runner for the orchestrator graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from apps.chat.src.agent.orchestrator.graph.housekeeping import OrchestratorHousekeeping
from apps.chat.src.agent.orchestrator.graph.invocation_runner import GraphConfigFactory
from banking.presentation.i18n.locale import LocaleManager
from shared.observability.llm import build_llm_runnable_config
from shared.utils.logging import log_fingerprint


@dataclass(slots=True)
class GraphResumeRunner:
    graph: CompiledStateGraph
    housekeeping: OrchestratorHousekeeping
    get_config: GraphConfigFactory
    logger: Any

    async def run(self, *, phone_number: str, payload: dict[str, Any], channel: str) -> dict[str, Any]:
        inputs = {
            "user_id": phone_number,
            "phone_number": phone_number,
            "last_callback": payload,
            "has_quote": False,
            "quoted_message_id": None,
        }

        graph_config = self.get_config(phone_number, channel=channel)
        config = graph_config.config

        self.logger.info(
            "orchestrator_graph_resume",
            phone_hash=log_fingerprint(phone_number),
            payload_keys=sorted(payload.keys()),
        )

        try:
            trace_config = build_llm_runnable_config(
                role="orchestrator_graph",
                channel=channel,
                path_label="interrupt_path",
                phone_number=phone_number,
                task_domain="resume",
                extra_metadata={"payload_keys": sorted(payload.keys())},
            )
            if trace_config:
                config["tags"] = list(trace_config.get("tags", []))
                config["metadata"] = dict(trace_config.get("metadata", {}))
            final_state = await self.graph.ainvoke(inputs, config=config)
            resolved_locale = LocaleManager.normalize((final_state.get("loaded_context") or {}).get("language")).value

            await self.housekeeping.run(
                thread_id=graph_config.thread_id,
                state=final_state,
                phone_number=phone_number,
                path_label="interrupt_path",
            )

            return {
                "text": final_state.get("final_response"),
                "outbox": final_state.get("outbox", []),
                "locale": resolved_locale,
            }
        except Exception as e:
            self.logger.exception("graph_resume_error", error=str(e))
            return {"text": None, "outbox": [], "locale": LocaleManager.DEFAULT_LOCALE.value}


__all__ = ["GraphResumeRunner"]
