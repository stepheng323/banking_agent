"""Tests for context-read fastpath planner behavior."""

import time

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import ContextFrameFollowupDecision, PlannerOutput


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _CapturingPlanner(_MockPlanner):
    def __init__(self, output: PlannerOutput) -> None:
        super().__init__(output)
        self.last_context: str | None = None

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text
        self.last_context = context
        return self._output


class _FailingPlanner:
    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: object | None = None,
        path_label: str = "planner_path",
    ) -> PlannerOutput:
        del phone_number, text, context, prompt_signals
        raise AssertionError("planner should not be called for grounded beneficiary detail follow-up")


class _FrameFollowupPlanner(_FailingPlanner):
    def __init__(self, decision: ContextFrameFollowupDecision) -> None:
        self.decision = decision
        self.last_context: str | None = None

    async def interpret_context_frame_followup(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameFollowupDecision:
        del phone_number, text, path_label
        self.last_context = context
        return self.decision


@pytest.mark.asyncio
async def test_fastpath_uses_direct_response_when_context_is_sufficient() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="You have 3 linked accounts.",
        response_key=None,
        confidence=0.95,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="account_count",
        normalized_instruction="how many accounts do i have",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_1",
        phone_number="2348000000000",
        channel="telegram",
        last_message_text="How many accounts do I have?",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001"},
                {"bank_name": "GTBank", "account_number": "0002"},
                {"bank_name": "Access Bank", "account_number": "0003"},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "You have 3 linked accounts."
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_falls_back_to_worker_when_context_missing() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="You have 4 beneficiaries.",
        response_key=None,
        confidence=0.91,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="beneficiary_count",
        normalized_instruction="how many beneficiaries do i have",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_2",
        phone_number="2348111111111",
        channel="whatsapp",
        last_message_text="How many beneficiaries do I have?",
        loaded_context={"accounts": []},
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    task = updates["tasks"]["t1"]
    assert task.type == "beneficiary"
    assert task.payload.get("action") == "list_beneficiaries"
    assert "final_response" not in updates


@pytest.mark.asyncio
async def test_fastpath_falls_back_when_planner_shape_is_not_conversational() -> None:
    planner_output = PlannerOutput(
        primary_intent="account",
        response="First Bank (...0001) is your default account.",
        response_key=None,
        confidence=0.93,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="default_account_identity",
        normalized_instruction="which account is default",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_3",
        phone_number="2348222222222",
        channel="whatsapp",
        last_message_text="Which account is default?",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001", "is_default": True},
                {"bank_name": "GTBank", "account_number": "0002", "is_default": False},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    task = updates["tasks"]["t1"]
    assert task.type == "account"
    assert task.payload.get("action") == "list_accounts"
    assert "final_response" not in updates


@pytest.mark.asyncio
async def test_fastpath_is_language_agnostic_when_planner_sets_subtype() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="O ni account meta.",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="Yoruba",
        context_read_subtype="account_count",
        normalized_instruction="melo ni account mi",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_4",
        phone_number="2348333333333",
        channel="whatsapp",
        last_message_text="Melo ni account mi?",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001"},
                {"bank_name": "GTBank", "account_number": "0002"},
                {"bank_name": "Access Bank", "account_number": "0003"},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "O ni account meta."
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_account_mandate_readiness_summary_uses_direct_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="2 accounts are ready and 1 is pending mandate.",
        response_key=None,
        confidence=0.93,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="account_mandate_readiness_summary",
        normalized_instruction="which of my accounts are ready",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_1",
        phone_number="2348444444444",
        channel="whatsapp",
        last_message_text="Which of my accounts are ready?",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001", "mandate_status": "ready"},
                {"bank_name": "GTBank", "account_number": "0002", "mandate_status": "ready"},
                {"bank_name": "Zenith", "account_number": "0003", "mandate_status": "pending"},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "2 accounts are ready and 1 is pending mandate."
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_account_linked_bank_existence_check_falls_back_when_accounts_missing() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="Yes, you have Zenith linked.",
        response_key=None,
        confidence=0.89,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="account_linked_bank_existence_check",
        normalized_instruction="do i have zenith linked",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_2",
        phone_number="2348555555555",
        channel="telegram",
        last_message_text="Do I have Zenith linked?",
        loaded_context={"accounts": []},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    task = updates["tasks"]["t1"]
    assert task.type == "account"
    assert task.payload.get("action") == "list_accounts"
    assert "final_response" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_account_linked_bank_existence_check_synthesizes_pending_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="*Your Bank Accounts*\n\n1. Zenith Bank (****9384) [✓]",
        response_key=None,
        confidence=0.92,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="account_linked_bank_existence_check",
        normalized_instruction="what about first bank",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_2c",
        phone_number="2348555555557",
        channel="telegram",
        last_message_text="What about First Bank?",
        loaded_context={
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"},
                {
                    "bank_name": "First Bank",
                    "account_number": "0334555167",
                    "mandate_status": "pending",
                    "extra_data": {
                        "transfer_destinations": [{"bank_name": "NIBSS Bank", "account_number": "0001112223"}]
                    },
                },
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert "First Bank linked" in updates.get("final_response", "")
    assert "not ready for payments yet" in updates.get("final_response", "")
    assert "transfer ₦50" in updates.get("final_response", "").lower()
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_account_linked_bank_existence_check_synthesizes_absent_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="*Your Bank Accounts*\n\n1. Zenith Bank (****9384) [✓]",
        response_key=None,
        confidence=0.92,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="account_linked_bank_existence_check",
        normalized_instruction="what about first bank",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_2d",
        phone_number="2348555555558",
        channel="telegram",
        last_message_text="What about First Bank?",
        loaded_context={
            "accounts": [
                {"bank_name": "Zenith Bank", "account_number": "00009384", "mandate_status": "ready"},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "No, you do not have First Bank linked."
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_link_account_request_bypasses_account_summary_read_path() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="Here are your linked accounts.",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="linked_accounts_summary",
        account_action_hint="link",
        normalized_instruction="link account",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_2b",
        phone_number="2348555555556",
        channel="whatsapp",
        last_message_text="Link account",
        loaded_context={
            "accounts": [
                {"bank_name": "Zenith", "account_number": "9384"},
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _MockPlanner(planner_output),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    task = updates["tasks"]["t1"]
    assert task.type == "account"
    assert task.payload.get("action") == "link"
    assert "final_response" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_beneficiary_name_match_preview_uses_direct_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="You have these Tolu beneficiaries: Tolu Adebayo (...0001), Tolu Adeyemi (...0002), Tolulope Johnson (...0003).",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="beneficiary_name_match_preview",
        normalized_instruction="which tolu do i have",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_3",
        phone_number="2348666666666",
        channel="whatsapp",
        last_message_text="Which Tolu do I have?",
        loaded_context={
            "beneficiaries": [
                {
                    "alias": "Tolu Access",
                    "account_name": "Tolu Adebayo",
                    "bank_name": "Access",
                    "account_number": "11110001",
                },
                {
                    "alias": "Tolu GTB",
                    "account_name": "Tolu Adeyemi",
                    "bank_name": "GTBank",
                    "account_number": "11110002",
                },
                {
                    "alias": "Tolu First",
                    "account_name": "Tolulope Johnson",
                    "bank_name": "First Bank",
                    "account_number": "11110003",
                },
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response", "").startswith("You have these Tolu beneficiaries:")
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_v2_flow_recap_without_active_flow_returns_no_active_flow_message() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="",
        response_key=None,
        confidence=0.86,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="flow_recap",
        normalized_instruction="where did we stop",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_4",
        phone_number="2348777777000",
        channel="whatsapp",
        last_message_text="Where did we stop?",
        loaded_context={},
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert (
        updates.get("final_response")
        == "There is no active transfer flow right now. Start a transfer and I will guide you."
    )
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_recent_domain_focus_is_injected_for_follow_up_binding() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="Here are your linked accounts.",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="linked_accounts_summary",
        normalized_instruction="list them",
        tasks=[],
    )
    planner = _CapturingPlanner(planner_output)
    state = OrchestratorState(
        user_id="u_focus_1",
        phone_number="2348999999000",
        channel="telegram",
        last_message_text="List them",
        loaded_context={
            "accounts": [
                {"bank_name": "First Bank", "account_number": "0001"},
                {"bank_name": "GTBank", "account_number": "0002"},
            ],
            "history": [
                {"role": "user", "content": "How many accounts do I have linked"},
                {"role": "assistant", "content": "You have 2 linked accounts."},
            ],
        },
        planner_output=PlannerOutput(
            primary_intent="conversational",
            response="You have 2 linked accounts.",
            response_key=None,
            confidence=0.93,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype="account_count",
            normalized_instruction="how many accounts do i have linked",
            tasks=[],
        ),
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "Here are your linked accounts."
    assert planner.last_context and "Recent Domain Focus: account" in planner.last_context


@pytest.mark.asyncio
async def test_fastpath_v2_flow_recap_with_active_interrupt_uses_direct_response() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="We are waiting for your beneficiary selection.",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="flow_recap",
        normalized_instruction="where did we stop",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_v2_5",
        phone_number="2348777777001",
        channel="whatsapp",
        last_message_text="Where did we stop?",
        pending_interrupt=PendingInterrupt(kind="input", task_ids=["t1"], fields_by_task={"t1": ["beneficiary_id"]}),
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 5000, "recipient_name": "Tolu"},
            )
        },
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)
    assert updates.get("final_response") == "We are waiting for your beneficiary selection."
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_fastpath_beneficiary_list_persists_context_frame() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="You have 1 saved beneficiary: Mum (Opay ...1023).",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="beneficiary_list",
        normalized_instruction="show my beneficiaries",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_fastpath_frame_1",
        phone_number="2348111000000",
        channel="whatsapp",
        last_message_text="Show my beneficiaries",
        loaded_context={
            "beneficiaries": [
                {
                    "id": "bene-1",
                    "alias": "Mum",
                    "account_name": "Mama Nkechi",
                    "account_number": "8162511023",
                    "bank_name": "Opay",
                    "bank_code": "100004",
                    "beneficiary_type": "transfer",
                }
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates.get("final_response") == "You have 1 saved beneficiary: Mum (Opay ...1023)."
    assert updates.get("context_frames")
    assert state.context_frames
    assert state.context_frames[-1].frame_type == ContextFrameType.BENEFICIARY_LIST
    assert state.context_frames[-1].items[0].label == "Mum"


@pytest.mark.asyncio
async def test_fastpath_beneficiary_name_preview_persists_context_frame() -> None:
    planner_output = PlannerOutput(
        primary_intent="conversational",
        response="You have these Tolu beneficiaries: Tolu Adebayo (...0001).",
        response_key=None,
        confidence=0.9,
        is_complex=False,
        is_cancellation=False,
        is_confirmation=False,
        detected_language="English",
        context_read_subtype="beneficiary_name_match_preview",
        normalized_instruction="which tolu do i have",
        tasks=[],
    )
    state = OrchestratorState(
        user_id="u_fastpath_frame_2",
        phone_number="2348111000001",
        channel="whatsapp",
        last_message_text="Which Tolu do I have?",
        loaded_context={
            "beneficiaries": [
                {
                    "id": "bene-2",
                    "alias": "Tolu",
                    "account_name": "Tolu Adebayo",
                    "account_number": "11110001",
                    "bank_name": "Access Bank",
                    "bank_code": "044",
                    "beneficiary_type": "transfer",
                }
            ]
        },
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates.get("final_response") == "You have these Tolu beneficiaries: Tolu Adebayo (...0001)."
    assert updates.get("context_frames")
    assert state.context_frames
    assert state.context_frames[-1].frame_type == ContextFrameType.BENEFICIARY_LIST
    assert state.context_frames[-1].items[0].data["id"] == "bene-2"


@pytest.mark.asyncio
async def test_beneficiary_detail_followup_answers_directly_from_recent_frame() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_bene_details_1",
        phone_number="2348111000002",
        channel="telegram",
        last_message_text="Show their full details",
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Mum",
                        data={
                            "alias": "Mum",
                            "account_name": "MERCY JOHNSON",
                            "bank_name": "Opay",
                            "account_number": "8162511023",
                        },
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Gaines",
                        data={
                            "alias": "Gaines",
                            "account_name": "Yusuf Ibrahim",
                            "bank_name": "Access Bank",
                            "account_number": "0000005262",
                        },
                    ),
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(decision="detail_request", confidence=0.96)
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    response = updates.get("final_response", "")
    assert response.startswith("Saved Beneficiary Details")
    assert "1. Mum" in response
    assert "Account Name: MERCY JOHNSON" in response
    assert "Account Number: 8162511023" in response
    assert "2. Gaines" in response
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_beneficiary_completeness_followup_answers_from_recent_frame() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_bene_complete_1",
        phone_number="2348111000004",
        channel="telegram",
        last_message_text="Is that all?",
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={"alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-3",
                        label="Tolu First",
                        data={"alias": "Tolu First", "account_name": "Tolulope Johnson"},
                    ),
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(decision="completeness_check", confidence=0.94)
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates.get("final_response") == "Yes. Those are the 3 saved beneficiaries I found."
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_account_completeness_followup_answers_from_recent_frame() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_account_complete_1",
        phone_number="2348111000005",
        channel="telegram",
        last_message_text="Any other one?",
        context_frames=[
            ContextFrame(
                frame_id="accounts_recent",
                frame_type=ContextFrameType.ACCOUNT_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-1",
                        label="First Bank",
                        data={"bank_name": "First Bank", "account_number": "6000000001"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.ACCOUNT,
                        entity_id="acct-2",
                        label="GTBank",
                        data={"bank_name": "GTBank", "account_number": "2000000002"},
                    ),
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(decision="completeness_check", confidence=0.93)
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates.get("final_response") == "Yes. Those are the 2 linked accounts I found."
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_beneficiary_lookup_followup_answers_no_match_from_recent_frame() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_bene_lookup_1",
        phone_number="2348111000006",
        channel="telegram",
        last_message_text="What about gaines",
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_lookup",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={"alias": "Tolu GTB", "account_name": "Tolu Adeyemi"},
                    ),
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(
                    decision="entity_lookup",
                    confidence=0.95,
                    detected_language="English",
                    target_text="gaines",
                )
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates.get("final_response") == "I don't see Gaines in the saved beneficiaries I showed."
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_beneficiary_lookup_followup_returns_matching_entities_from_recent_frame() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_bene_lookup_2",
        phone_number="2348111000007",
        channel="telegram",
        last_message_text="What about tolu",
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_lookup_match",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={
                            "alias": "Tolu Access",
                            "account_name": "Tolu Adebayo",
                            "bank_name": "Access Bank",
                            "account_number": "2010000001",
                        },
                    ),
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-2",
                        label="Tolu GTB",
                        data={
                            "alias": "Tolu GTB",
                            "account_name": "Tolu Adeyemi",
                            "bank_name": "GTBank",
                            "account_number": "2010000002",
                        },
                    ),
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(
                    decision="entity_lookup",
                    confidence=0.95,
                    detected_language="English",
                    target_text="tolu",
                )
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    response = updates.get("final_response")
    assert response is not None
    assert "I found 2 matches in the saved beneficiaries I showed." in response
    assert "Tolu Access" in response
    assert "Tolu GTB" in response
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_multilingual_frame_followup_uses_semantic_decision_not_english_phrase_match() -> None:
    now = int(time.time())
    planner = _FrameFollowupPlanner(
        ContextFrameFollowupDecision(
            decision="entity_lookup",
            confidence=0.92,
            detected_language="Yoruba",
            target_text="gaines",
        )
    )
    state = OrchestratorState(
        user_id="u_bene_lookup_3",
        phone_number="2348111000008",
        channel="telegram",
        last_message_text="Gaines nko?",
        context_frames=[
            ContextFrame(
                frame_id="beneficiaries_recent_lookup_yoruba",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Tolu Access",
                        data={"alias": "Tolu Access", "account_name": "Tolu Adebayo"},
                    )
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "services": {}, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert "Frame type: beneficiary_list" in (planner.last_context or "")
    assert updates.get("final_response") == "I don't see Gaines in the saved beneficiaries I showed."
    assert updates.get("semantic_path_shape") == "context_frame_followup"
    assert "tasks" not in updates


@pytest.mark.asyncio
async def test_single_beneficiary_detail_followup_returns_single_block() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="u_bene_details_2",
        phone_number="2348111000003",
        channel="telegram",
        last_message_text="Show the beneficiary details",
        context_frames=[
            ContextFrame(
                frame_id="beneficiary_recent_single",
                frame_type=ContextFrameType.BENEFICIARY_LIST,
                items=[
                    ContextEntity(
                        entity_type=EntityType.BENEFICIARY,
                        entity_id="bene-1",
                        label="Mum",
                        data={
                            "alias": "Mum",
                            "account_name": "MERCY JOHNSON",
                            "bank_name": "Opay",
                            "account_number": "8162511023",
                        },
                    )
                ],
                created_at_ts=now,
                ttl_seconds=600,
            )
        ],
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": _FrameFollowupPlanner(
                ContextFrameFollowupDecision(decision="detail_request", confidence=0.96)
            ),
            "services": {},
            "redis_client": None,
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    response = updates.get("final_response", "")
    assert response.startswith("Beneficiary Details")
    assert "Mum" in response
    assert "Account Name: MERCY JOHNSON" in response
    assert "tasks" not in updates
