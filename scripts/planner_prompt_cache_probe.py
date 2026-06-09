"""Repeat one planner prompt/schema to inspect provider prompt-cache behavior."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, cast

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import (
    PLANNER_USER_PROMPT_TEMPLATE,
    _augment_context_with_clean_transfer_hint,
    _planner_prompt_cache_key,
    _planner_response_model_for_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_model_wiring import with_structured_output
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_observability import invoke_structured_prompt
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptSignals,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_runtime import (
    build_runtime_planner_system_prompt,
)
from shared.config.settings import settings
from shared.observability.llm_call_metrics import start_llm_call_recording, stop_llm_call_recording
from shared.observability.llm_http import build_llm_http_async_client
from shared.types.planner import TransactionExecutor


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


async def _run_probe(args: argparse.Namespace) -> dict[str, Any]:
    executors = cast(tuple[TransactionExecutor, ...], tuple(item.strip() for item in args.executors.split(",") if item))
    prompt_signals = PlannerPromptSignals(
        expected_transaction_executors=executors,
        compact_context=args.compact_context,
    )
    context = _augment_context_with_clean_transfer_hint(args.context, args.text, prompt_signals)
    prompt_input = PlannerPromptBuildInput(text=args.text, context=context, signals=prompt_signals)
    prompt_result = build_runtime_planner_system_prompt(prompt_input)
    response_type = _planner_response_model_for_prompt(prompt_signals, prompt_result)
    user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(
        phone_number=args.phone,
        user_message=args.text,
        context=context,
    )
    llm = ChatOpenAI(
        model=args.model,
        temperature=0,
        timeout=args.timeout,
        max_retries=1,
        http_async_client=build_llm_http_async_client(),
        include_response_headers=True,
    )
    structured_llm = with_structured_output(llm, response_type, method="function_calling")
    prompt_cache_key = None if args.disable_prompt_cache_key else _planner_prompt_cache_key(response_type)
    calls: list[dict[str, Any]] = []
    logger = _Logger()

    for index in range(args.iterations + args.warmup):
        token = start_llm_call_recording()
        call_start = time.perf_counter()
        try:
            await invoke_structured_prompt(
                structured_llm,
                response_type,
                system_prompt=prompt_result.system_prompt,
                user_prompt=user_prompt,
                logger=logger,
                event_name="planner_llm_call",
                model_llm=llm,
                path_label="planner_probe",
                latency_span="planner_llm",
                log_fields={
                    "probe_iteration": index + 1,
                    "probe_warmup": index < args.warmup,
                    "prompt_profile": prompt_result.profile,
                    "prompt_bundles": list(prompt_result.selected_bundle_ids),
                    "planner_response_model": response_type.__name__,
                },
                prompt_cache_key=prompt_cache_key,
            )
        finally:
            recorded = stop_llm_call_recording(token)
        call = dict(recorded[-1]) if recorded else {}
        call["wall_duration_ms"] = round((time.perf_counter() - call_start) * 1000, 2)
        if index >= args.warmup:
            calls.append(call)

    return {
        "model": args.model,
        "text": args.text,
        "executors": list(executors),
        "iterations": args.iterations,
        "warmup": args.warmup,
        "prompt_cache_key": prompt_cache_key,
        "response_type": response_type.__name__,
        "prompt_profile": prompt_result.profile,
        "prompt_bundles": list(prompt_result.selected_bundle_ids),
        "system_chars": len(prompt_result.system_prompt),
        "user_chars": len(user_prompt),
        "calls": calls,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=settings.planner_model)
    parser.add_argument("--phone", default="2348162511023")
    parser.add_argument("--text", default="Send 2k each to Tolu Access and Tolu GTB")
    parser.add_argument("--context", default="None")
    parser.add_argument("--executors", default="transfer")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--compact-context", action="store_true")
    parser.add_argument("--disable-prompt-cache-key", action="store_true")
    parser.add_argument(
        "--json-output",
        default="logs/readiness/planner-prompt-cache-probe.json",
    )
    return parser


async def main() -> None:
    args = _parser().parse_args()
    report = await _run_probe(args)
    output_path = Path(args.json_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[report] wrote {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
