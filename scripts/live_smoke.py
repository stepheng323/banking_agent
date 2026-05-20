"""Automated live-dry-run smoke test for the chat orchestrator.

This runs the real local orchestrator stack in-process against the configured DB,
Redis, providers, and LLMs. It does not call Telegram or WhatsApp delivery APIs.

Usage:
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --seed
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --scenario mvp
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --scenario query-deep
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_openai import ChatOpenAI

# Allow direct execution as `python scripts/live_smoke.py`.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.chat.src.runtime.chat_worker_dependencies import (  # noqa: E402
    _build_orchestrator_runtime_bundle,
    _resolve_role_model,
)
from scripts.seed_user_test_data import _resolve_target_user, _seed_for_user  # noqa: E402
from shared.assistant_profile.loader import get_cached_assistant_profile  # noqa: E402
from shared.cache.redis_client import RedisClient  # noqa: E402
from shared.config.settings import settings  # noqa: E402
from shared.guardrails.loader import get_cached_guardrails  # noqa: E402
from shared.i18n import validate_catalog_completeness  # noqa: E402
from shared.policy.loader import get_cached_policy  # noqa: E402
from shared.policy.validation import validate_policy_coverage  # noqa: E402
from shared.repositories.user_repository import UserRepository  # noqa: E402
from shared.services.task_planner import refresh_planner_system_prompt  # noqa: E402

ScenarioName = Literal["quick", "mvp", "query", "query-deep"]


@dataclass(frozen=True)
class SmokeTurn:
    text: str
    expect_any: tuple[str, ...] = ()
    expect_all: tuple[str, ...] = ()
    expect_none: tuple[str, ...] = ()
    pin_after: bool = False
    pin_flow_type: str = "transaction"


@dataclass
class SmokeResult:
    turn: SmokeTurn
    response_text: str
    passed: bool
    errors: list[str] = field(default_factory=list)


class NoopPublisher:
    """Capture async jobs without publishing them to Redis/SQS."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.messages.append({"topic": topic, "message": message})


def _smoke_turns(scenario: ScenarioName) -> list[SmokeTurn]:
    query = [
        SmokeTurn("Hi", expect_any=("what would you like", "help", "narya")),
        SmokeTurn("Show my recent transactions", expect_any=("transaction", "showing", "sent", "received")),
        SmokeTurn("Show the 25k one", expect_any=("25,000", "transaction details", "adebayo", "bank")),
        SmokeTurn("What bank was that?", expect_any=("bank", "zenith", "first", "gtbank", "access")),
        SmokeTurn("Now show the 3rd transaction", expect_any=("transaction details", "amount", "bank")),
        SmokeTurn("Back", expect_any=("transaction", "showing", "more", "page")),
        SmokeTurn("More", expect_any=("transaction", "showing", "more", "page")),
    ]
    query_deep = [
        *query,
        SmokeTurn("Is this all?", expect_any=("coverage", "local", "synced", "confirm", "complete")),
        SmokeTurn("Break down my spending by account this month", expect_any=("breakdown", "account", "bank")),
        SmokeTurn("Which account did I spend from most?", expect_any=("account", "spent", "₦", "bank")),
        SmokeTurn("How much did I spend on food this month?", expect_any=("spent", "food", "₦", "transaction")),
        SmokeTurn("Show the transactions behind that", expect_any=("transaction", "showing", "sent", "received")),
        SmokeTurn("Can I send 35k?", expect_any=("cover", "₦35,000", "available", "shortfall", "breakdown")),
        SmokeTurn("Why are Zenith transactions missing?", expect_any=("zenith", "coverage", "authorization", "sync")),
    ]
    quick = [
        SmokeTurn("Hi", expect_any=("what would you like", "help", "narya")),
        SmokeTurn("Show my beneficiaries", expect_any=("beneficiar", "tolu")),
        SmokeTurn("Is that all?", expect_any=("3", "beneficiar", "saved")),
        SmokeTurn("Show my accounts", expect_any=("account", "bank")),
        SmokeTurn("Which one is GTBank?", expect_any=("gtbank", "0002", "account")),
        SmokeTurn("Why is Zenith pending?", expect_any=("zenith", "pending", "authorization")),
        SmokeTurn("Send 2k to tolu", expect_any=("tolu", "confirm", "which")),
    ]
    if scenario == "quick":
        return quick
    if scenario == "query":
        return query
    if scenario == "query-deep":
        return query_deep

    return [
        SmokeTurn("Hi", expect_any=("what would you like", "help", "narya")),
        SmokeTurn("Show my beneficiaries", expect_any=("beneficiar", "tolu")),
        SmokeTurn("Is that all?", expect_any=("3", "beneficiar", "saved")),
        SmokeTurn("Show my accounts", expect_any=("account", "bank")),
        SmokeTurn("Which one is GTBank?", expect_any=("gtbank", "0002", "account")),
        SmokeTurn("Why is Zenith pending?", expect_any=("zenith", "pending", "authorization")),
        SmokeTurn("Send 2k to tolu adebayo", expect_any=("tolu", "confirm", "access")),
        SmokeTurn("Also buy me 1k airtime", expect_any=("airtime", "confirm", "081")),
        SmokeTurn("The transfer narration should be groceries", expect_any=("groceries", "airtime")),
        SmokeTurn("Remove airtime", expect_none=("airtime",)),
        SmokeTurn("Sorry add it back", expect_any=("airtime", "confirm")),
        SmokeTurn("Change airtime amount to 2k", expect_any=("airtime", "2,000")),
    ]


def _build_chat_model(*, role: str, model: str, timeout: float) -> ChatOpenAI:
    print(f"[setup] {role} model: {model}")
    return ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=1)


def _intent_to_dict(intent: Any) -> dict[str, Any]:
    if hasattr(intent, "to_dict"):
        data = intent.to_dict()
        if isinstance(data, dict):
            return data
    if isinstance(intent, dict):
        return intent
    return {"type": type(intent).__name__, "value": str(intent)}


def _stringify_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _render_orchestrator_result(result: dict[str, Any]) -> str:
    outbox = result.get("outbox") or []
    rendered: list[str] = []
    for entry in outbox:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "say":
            rendered.append(_stringify_payload(entry.get("text")))
        elif entry_type == "request_confirmation":
            header = _stringify_payload(entry.get("header"))
            summary = _stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part))
        elif entry_type == "auth_request":
            header = _stringify_payload(entry.get("header"))
            summary = _stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part) or "PIN authorization requested.")
        elif entry_type == "show_options":
            title = _stringify_payload(entry.get("title"))
            options = entry.get("options") or []
            option_lines = []
            for idx, option in enumerate(options, start=1):
                if isinstance(option, dict):
                    label = option.get("label") or option.get("title") or option.get("text") or option
                    option_lines.append(f"{idx}. {label}")
                else:
                    option_lines.append(f"{idx}. {option}")
            rendered.append("\n".join([title, *option_lines]).strip())
        elif entry_type == "show_receipt":
            receipt = entry.get("receipt") or {}
            caption = _stringify_payload(entry.get("caption"))
            rendered.append("\n".join(part for part in (caption, str(receipt)) if part))
        else:
            rendered.append(str(entry))

    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    intents = result.get("intents") or []
    for intent in intents:
        data = _intent_to_dict(intent)
        rendered.append(
            "\n".join(
                part
                for part in (
                    _stringify_payload(data.get("header")),
                    _stringify_payload(data.get("summary")),
                    _stringify_payload(data.get("text")),
                    _stringify_payload(data.get("fallback_text")),
                )
                if part
            )
        )
    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    return _stringify_payload(result.get("text")).strip()


def _assert_turn(turn: SmokeTurn, response: str) -> tuple[bool, list[str]]:
    lowered = response.lower()
    errors: list[str] = []
    if turn.expect_any and not any(expected.lower() in lowered for expected in turn.expect_any):
        errors.append(f"expected any of: {', '.join(turn.expect_any)}")
    for expected in turn.expect_all:
        if expected.lower() not in lowered:
            errors.append(f"expected: {expected}")
    for forbidden in turn.expect_none:
        if forbidden.lower() in lowered:
            errors.append(f"did not expect: {forbidden}")
    return not errors, errors


async def _reset_redis_session(*, redis_client: Any, phone: str, channel: str) -> int:
    patterns = [
        f"checkpoint:{channel}:{phone}:*",
        f"checkpoint_write:{channel}:{phone}:*",
        f"write_keys_zset:{channel}:{phone}:*",
        f"user:{phone}:chat_history",
        f"query:session:{phone}",
        f"context_frames:{phone}",
    ]
    deleted = 0
    for pattern in patterns:
        keys = [key async for key in redis_client.scan_iter(match=pattern)]
        if keys:
            deleted += int(await redis_client.delete(*keys))
    return deleted


async def _build_agent() -> tuple[UserRepository, OrchestratorAgent, NoopPublisher, Any]:
    validate_catalog_completeness()
    capability_policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(capability_policy)
    get_cached_assistant_profile(force_reload=True)
    get_cached_guardrails(force_reload=True)
    refresh_planner_system_prompt()

    shared_redis = RedisClient.get_client()
    publisher = NoopPublisher()
    planner_llm = _build_chat_model(role="planner", model=settings.planner_model, timeout=30.0)
    app_env = settings.runtime.app_env
    query_model = _resolve_role_model(
        role="query",
        configured_model=settings.query_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    semantic_router_model = _resolve_role_model(
        role="semantic_router",
        configured_model=settings.semantic_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    interrupt_model = _resolve_role_model(
        role="interrupt_router",
        configured_model=settings.interrupt_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    extractor_model = _resolve_role_model(
        role="extractor",
        configured_model=settings.extractor_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )

    user_repo, _onboarding_executor, agent = _build_orchestrator_runtime_bundle(
        queue_publisher=publisher,
        messaging_clients={},
        shared_redis=shared_redis,
        llm=planner_llm,
        query_llm=_build_chat_model(role="query", model=query_model, timeout=30.0),
        semantic_router_llm=_build_chat_model(role="semantic_router", model=semantic_router_model, timeout=15.0),
        interrupt_llm=_build_chat_model(role="interrupt_router", model=interrupt_model, timeout=15.0),
        extractor_llm=_build_chat_model(role="extractor", model=extractor_model, timeout=20.0),
    )

    async def _noop_progress_updates(**_: Any) -> None:
        return None

    # This is a live dry-run: do not send channel typing/progress signals.
    agent.orchestrator_handler._run_progress_updates = _noop_progress_updates
    return user_repo, agent, publisher, shared_redis


async def _run_smoke(args: argparse.Namespace) -> int:
    target_user = await _resolve_target_user(args.phone)
    if args.seed:
        await _seed_for_user(target_user)

    user_repo, agent, publisher, redis_client = await _build_agent()
    user = await user_repo.get_by_phone(target_user.phone_number)
    if user is None:
        raise RuntimeError(f"Could not load user {target_user.phone_number}")

    if args.reset_session:
        deleted = await _reset_redis_session(
            redis_client=redis_client,
            phone=target_user.phone_number,
            channel=args.channel,
        )
        print(f"[setup] reset Redis session keys: {deleted}")

    print("\n=== Automated Live Dry-Run Smoke ===")
    print(f"phone={target_user.phone_number} channel={args.channel} scenario={args.scenario}")
    print("delivery=disabled queue_publish=captured\n")

    results: list[SmokeResult] = []
    run_id = uuid.uuid4().hex[:8]
    for idx, turn in enumerate(_smoke_turns(args.scenario), start=1):
        message_id = f"live-smoke-{run_id}-{idx}-{int(time.time() * 1000)}"
        print(f"USER {idx}: {turn.text}")
        started = time.perf_counter()
        response = await agent.invoke(
            phone_number=target_user.phone_number,
            text=turn.text,
            message_id=message_id,
            channel=args.channel,
            channel_identity=args.channel_user_id,
            user=user,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        rendered = _render_orchestrator_result(response)
        passed, errors = _assert_turn(turn, rendered)
        print(f"NARYA ({elapsed_ms:.0f}ms):\n{rendered or '[no visible response]'}")

        if turn.pin_after:
            pin_response = await agent.resume_transaction(
                phone_number=target_user.phone_number,
                flow_type=turn.pin_flow_type,
                pin_verified=True,
                channel=args.channel,
            )
            rendered_pin = _render_orchestrator_result(pin_response)
            print(f"PIN CALLBACK:\n{rendered_pin or '[no visible response]'}")
            rendered = "\n\n".join(part for part in (rendered, rendered_pin) if part)

        status = "PASS" if passed else "FAIL"
        print(f"[{status}]\n")
        results.append(SmokeResult(turn=turn, response_text=rendered, passed=passed, errors=errors))

        if args.stop_on_fail and not passed:
            break

    failed = [result for result in results if not result.passed]
    print("=== Smoke Summary ===")
    print(f"turns={len(results)} passed={len(results) - len(failed)} failed={len(failed)}")
    print(f"captured_async_jobs={len(publisher.messages)}")
    if failed:
        for result in failed:
            print(f"- {result.turn.text}: {'; '.join(result.errors)}")
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an automated local live-dry-run MVP smoke test.")
    parser.add_argument("--phone", required=True, help="Existing test user's phone number.")
    parser.add_argument("--channel", default="telegram", choices=("telegram", "whatsapp"))
    parser.add_argument(
        "--channel-user-id",
        default=None,
        help="Optional channel user id to attach to the message context.",
    )
    parser.add_argument("--scenario", default="quick", choices=("quick", "mvp", "query", "query-deep"))
    parser.add_argument("--seed", action="store_true", help="Seed standard accounts and Tolu beneficiaries first.")
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Delete Redis session/checkpoint keys for this phone/channel.",
    )
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop after the first failed assertion.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_run_smoke(args)))


if __name__ == "__main__":
    main()
