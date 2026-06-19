import json

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks
from shared.types.planner import (
    BeneficiaryTaskParameters,
    PlannerOutput,
    TransferTaskParameters,
    make_planned_task,
)


class _SuggestionAwarePlanner:
    def __init__(self) -> None:
        self.last_prompt_signals = None

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
        self.last_prompt_signals = prompt_signals
        if getattr(prompt_signals, "has_beneficiary_suggestion", False):
            return PlannerOutput(
                primary_intent="beneficiary",
                response="",
                response_key=None,
                confidence=0.92,
                is_complex=False,
                is_cancellation=False,
                is_confirmation=False,
                detected_language="English",
                context_read_subtype=None,
                normalized_instruction="save beneficiary",
                tasks=[
                    make_planned_task(
                        task_id="t_save",
                        action="save_beneficiary",
                        executor="beneficiary",
                        instruction="save beneficiary",
                        parameters=BeneficiaryTaskParameters(alias="mom"),
                        risk="READ_ONLY",
                    )
                ],
            )

        return PlannerOutput(
            primary_intent="transfer",
            response="",
            response_key=None,
            confidence=0.95,
            is_complex=False,
            is_cancellation=False,
            is_confirmation=False,
            detected_language="English",
            context_read_subtype=None,
            normalized_instruction="send 10k to mom",
            tasks=[
                make_planned_task(
                    task_id="t_transfer",
                    action="send_money",
                    executor="transfer",
                    instruction="send 10k to mom",
                    parameters=TransferTaskParameters(amount=10000, recipient="mom"),
                    risk="MONEY_MOVE",
                )
            ],
        )


class _RedisWithPendingSuggestion:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        if ":beneficiary_suggestion" in key:
            return json.dumps(
                {
                    "recipient_name": "Tolu Adedayo",
                    "recipient_account": "0760505261",
                    "bank_name": "First Bank",
                }
            )
        if "query:session:" in key:
            return None
        return None

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


@pytest.mark.asyncio
async def test_transaction_turn_ignores_pending_beneficiary_save_prompt() -> None:
    planner = _SuggestionAwarePlanner()
    redis = _RedisWithPendingSuggestion()
    state = OrchestratorState(
        user_id="u_suggestion_bypass_1",
        phone_number="2348011112233",
        channel="whatsapp",
        last_message_text="Send 10k to mom",
        loaded_context={"language": "en"},
        tasks={},
        waves=[],
        current_wave_index=0,
    )
    config: RunnableConfig = {
        "configurable": {
            "task_planner": planner, "semantic_router_llm": planner, "capability_classifier_llm": planner,
            "redis_client": redis,
            "services": {},
        },
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert getattr(planner.last_prompt_signals, "has_beneficiary_suggestion", False) is False
    transfer_task = next(iter(updates["tasks"].values()))
    assert transfer_task.type == "transfer"
    assert transfer_task.payload.get("action") == "send_money"
    assert redis.deleted_keys == []
