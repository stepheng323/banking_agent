"""Integration tests for mixed-intent policy notice behavior in orchestrator nodes."""

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message
from apps.core.src.agent.orchestrator.nodes.planner import SAFE_CAPABILITY_FALLBACK, plan_tasks
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _MockPlanner:
    """Mock planner used by orchestrator node integration tests."""

    planner_llm: Any | None

    def __init__(self, output: PlannerOutput, planner_llm: Any | None = None):
        self._output = output
        self.planner_llm = planner_llm

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _MockTransferWorker:
    """Mock transfer worker that always requests more input."""

    async def run(
        self,
        payload: Any,
        context: Any,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, user_message, pin_verified
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account"],
            prompt="Provide recipient account details.",
        )


class _FakeRedis:
    """Minimal async redis stub for locale/update behavior in planner tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


def _apply(state: OrchestratorState, updates: dict) -> OrchestratorState:
    """Apply node updates to state."""
    return state.model_copy(update=updates)


@pytest.mark.asyncio
async def test_mixed_intent_outbox_contains_notice_then_transfer_prompt() -> None:
    """Mixed request should prepend unsupported notice then continue transfer flow."""
    planner_output = PlannerOutput(
        primary_intent="transfer",
        response="",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="send 10k to tolu and invest 10k",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to tolu",
                parameters=TaskParameters(amount="10000", recipient="tolu"),
                risk="MONEY_MOVE",
            )
        ],
    )

    state = OrchestratorState(
        user_id="u_1",
        phone_number="2348000000000",
        channel="whatsapp",
        last_message_text="send 10k to tolu and invest 10k",
    )

    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {"transfer": _MockTransferWorker()},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))
    state = _apply(state, await advance_wave(state, config))

    outbox = state.outbox
    assert len(outbox) >= 2
    assert "Investments" in outbox[0]["text"]
    assert "I can proceed with money transfer" in outbox[0]["text"]
    assert "I can help with send money or review recent transactions instead." in outbox[0]["text"]
    assert "I need account details for tolu." in outbox[1]["text"]
    assert state.policy_notice is None


@pytest.mark.asyncio
async def test_open_world_fallback_when_no_supported_tasks() -> None:
    """No supported tasks and no known unsupported class should use safe fallback."""
    planner_output = PlannerOutput(
        primary_intent="faq",
        response="",
        confidence=0.2,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="i want to transfer to pension fund",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_2",
        phone_number="2348111111111",
        channel="whatsapp",
        last_message_text="i want to transfer to pension fund",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == SAFE_CAPABILITY_FALLBACK


@pytest.mark.asyncio
async def test_conversational_response_key_renders_deterministically() -> None:
    """Conversational no-task replies should prefer keyed deterministic rendering."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.capability_question",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="How far",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_3",
        phone_number="2348222222222",
        channel="whatsapp",
        last_message_text="How far",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output, planner_llm=object()),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert (
        state.final_response
        == "I handle transfers, airtime/data, balance checks, and transaction queries."
    )


@pytest.mark.asyncio
async def test_conversational_planner_response_localizes_for_pidgin() -> None:
    """Conversational keyed response should localize for pidgin users."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.checkin",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="Pidgin",
        normalized_instruction="How far",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_4",
        phone_number="2348333333333",
        channel="whatsapp",
        last_message_text="How far",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output, planner_llm=object()),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "I dey here gidigba. Which money move make we run?"


@pytest.mark.asyncio
async def test_conversational_response_key_localizes_for_yoruba() -> None:
    """Greeting key should render in Yoruba when locale resolves to Yoruba."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.greeting",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="Yoruba",
        normalized_instruction="Bawo",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_5",
        phone_number="2348444444444",
        channel="whatsapp",
        last_message_text="Bawo",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output, planner_llm=object()),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "Pẹlẹ o. Bawo ni mo ṣe le ran ọ lọwọ pẹlu owo rẹ loni?"


@pytest.mark.asyncio
async def test_conversational_missing_response_key_uses_deterministic_clarify() -> None:
    """Missing conversational response_key should always use deterministic clarify fallback."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="hello there",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_6",
        phone_number="2348555555555",
        channel="whatsapp",
        last_message_text="hello there",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output, planner_llm=object()),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "Say exactly what you want me to do with your money."


@pytest.mark.asyncio
async def test_conversational_missing_response_key_falls_back_deterministically(
) -> None:
    """Missing key fallback should stay localized and deterministic."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="Pidgin",
        normalized_instruction="can you help me do crypto swaps",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_7",
        phone_number="2348666666666",
        channel="whatsapp",
        last_message_text="can you help me do crypto swaps",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "Talk am straight. Wetin exactly you want make I do with your money?"


@pytest.mark.asyncio
async def test_conversational_response_uses_detected_language_even_with_cached_pidgin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached pidgin locale should not force pidgin when this turn detects English."""
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.greeting",
        confidence=1.0,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="Hi",
        tasks=[],
    )

    fake_redis = _FakeRedis()
    phone = "2348770000000"
    fake_redis._store[f"user:{phone}:language"] = "pcm"

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    state = OrchestratorState(
        user_id="u_8",
        phone_number=phone,
        channel="whatsapp",
        last_message_text="Hi",
        loaded_context={"language": "pcm"},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": fake_redis,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "Hey. I'm Fusepay. What money move should we handle?"
    assert (state.loaded_context or {}).get("language") == "en"
