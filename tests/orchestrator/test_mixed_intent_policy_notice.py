"""Integration tests for mixed-intent policy notice behavior in orchestrator nodes."""

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.execution import advance_wave
from apps.core.src.agent.orchestrator.nodes.finalize import finalize
from apps.core.src.agent.orchestrator.nodes.ingest import ingest_message
from apps.core.src.agent.orchestrator.nodes.planner import SAFE_CAPABILITY_FALLBACK, plan_tasks
from shared.i18n import render_message
from shared.policy import get_cached_policy
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


class _MockTransferCompleteWorker:
    async def run(
        self,
        payload: Any,
        context: Any,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        raw_amount = payload.get("amount", 0)
        amount = float(raw_amount) if isinstance(raw_amount, (int, float, str)) else 0.0
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "amount": amount,
                "recipient_name": payload.get("recipient_name", "Recipient"),
            },
            receipt={"status": "success", "message": "processed"},
            response="Transfer completed",
        )


class _MockAccountWorker:
    async def run(
        self,
        payload: Any,
        context: Any,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> Any:
        del payload, context, user_message, pin_verified
        from apps.core.src.agent.orchestrator.models.domain import AccountOutcome, AccountResult

        return AccountResult(outcome=AccountOutcome.OK, response="Balance is available.")


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


class _FakeMetaLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def with_structured_output(self, _schema: Any) -> "_FakeMetaLLM":
        return self

    def with_config(self, _config: dict[str, Any]) -> "_FakeMetaLLM":
        return self

    async def ainvoke(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        return self._response


def _apply(state: OrchestratorState, updates: dict) -> OrchestratorState:
    """Apply node updates to state."""
    return state.model_copy(update=updates)


def _expected_policy_greeting(locale: str) -> str:
    policy = get_cached_policy()
    return render_message(
        "meta.fallback",
        locale,
        {
            "name": policy.identity.name,
            "description": policy.identity.description,
            "supported": ", ".join(policy.supported_domains),
        },
    )


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
        loaded_context={
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
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
    assert "account number for tolu" in outbox[1]["text"].lower()
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

    assert state.final_response == "I handle transfers, airtime/data, balance checks, and transaction queries."


@pytest.mark.asyncio
async def test_conversational_identity_uses_meta_llm_when_available() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.identity",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="who created you",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_meta_1",
        phone_number="2348999999991",
        channel="whatsapp",
        last_message_text="who created you",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                planner_llm=_FakeMetaLLM(
                    {"handoff": "meta", "language": "en", "message": "I am Narya AI, built by the Fuse team."}
                ),
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "I am Narya AI, built by the Fuse team."


@pytest.mark.asyncio
async def test_conversational_identity_falls_back_to_key_without_llm() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.identity",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="who created you",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_meta_2",
        phone_number="2348999999992",
        channel="whatsapp",
        last_message_text="who created you",
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

    assert state.final_response == render_message("conversational.identity", "en")


@pytest.mark.asyncio
async def test_conversational_capability_question_uses_meta_llm_when_available() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.capability_question",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="what can you do",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_meta_3",
        phone_number="2348999999993",
        channel="whatsapp",
        last_message_text="what can you do",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                planner_llm=_FakeMetaLLM(
                    {
                        "handoff": "meta",
                        "language": "en",
                        "message": "I can help with transfers, airtime, data, and account checks.",
                    }
                ),
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert state.final_response == "I can help with transfers, airtime, data, and account checks."


@pytest.mark.asyncio
async def test_conversational_out_of_scope_uses_meta_llm_when_available() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key="conversational.out_of_scope",
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        normalized_instruction="book me a flight",
        tasks=[],
    )

    state = OrchestratorState(
        user_id="u_meta_4",
        phone_number="2348999999994",
        channel="whatsapp",
        last_message_text="book me a flight",
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(
                planner_output,
                planner_llm=_FakeMetaLLM(
                    {
                        "handoff": "meta",
                        "language": "en",
                        "message": "I can’t book flights yet. I can help with transfers, airtime, or checking your account.",
                    }
                ),
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))

    assert (
        state.final_response
        == "I can’t book flights yet. I can help with transfers, airtime, or checking your account."
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

    assert state.final_response == render_message("conversational.checkin", "pcm")


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

    assert state.final_response == _expected_policy_greeting("yo")


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
async def test_conversational_missing_response_key_falls_back_deterministically() -> None:
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

    assert state.final_response == _expected_policy_greeting("en")
    assert (state.loaded_context or {}).get("language") == "en"


@pytest.mark.asyncio
async def test_mixed_request_runs_in_order_without_resume_prompt() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        response="",
        confidence=0.9,
        is_complex=True,
        detected_language="English",
        normalized_instruction="send 10k to mum and dad then show my balance",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount="10000", recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Dad",
                parameters=TaskParameters(amount="10000", recipient="Dad"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t3",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1", "t2"],
                risk="READ_ONLY",
            ),
        ],
    )

    state = OrchestratorState(
        user_id="u_9",
        phone_number="2348888888888",
        channel="whatsapp",
        last_message_text="send 10k to mum and dad then show my balance",
        loaded_context={
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {"transfer": _MockTransferCompleteWorker(), "account": _MockAccountWorker()},
            "redis_client": None,
            "queue": None,
            "beneficiary_suggestion_service": None,
        },
        "recursion_limit": 50,
    }

    state = _apply(state, await ingest_message(state))
    state = _apply(state, await plan_tasks(state, config))
    assert state.waves == [["t1", "t2"], ["t3"]]

    state = _apply(state, await advance_wave(state, config))
    assert state.current_wave_index == 1

    state = _apply(state, await advance_wave(state, config))
    assert state.current_wave_index == 2

    final_updates = await finalize(state, config)
    outbox = final_updates["outbox"]
    assert all("resume your transfer" not in item.get("text", "").lower() for item in outbox)
