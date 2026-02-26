from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.core.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.core.src.agent.orchestrator.models.message_context import MessageContext


class _CheckpointerStub:
    async def asetup(self) -> None:
        return

    async def adelete_thread(self, thread_id: str) -> None:
        del thread_id
        return


class _GraphStub:
    def __init__(self) -> None:
        self.last_inputs: dict | None = None

    async def ainvoke(self, inputs: dict, config: dict) -> dict:
        del config
        self.last_inputs = inputs
        return {
            "outbox": [],
            "final_response": None,
            "loaded_context": {"language": "en"},
        }


class _ContextManagerStub:
    async def load_context_parallel(self, phone_number: str):
        del phone_number
        return (
            {
                "profile": {"id": str(uuid4())},
                "accounts": [],
                "beneficiaries": [],
                "history": [],
                "language": "en",
            },
            None,
            None,
            None,
        )


@pytest.mark.asyncio
async def test_graph_handler_invoke_passes_quoted_message_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        whatsapp_client=SimpleNamespace(),
        queue=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )

    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    await handler.invoke(
        MessageContext(
            phone_number="2348000000001",
            text="this failed transfer",
            message_id="wamid.11",
            quoted_message_id="wamid.receipt.42",
            channel="whatsapp",
            channel_identity="2348000000001",
        )
    )

    assert graph.last_inputs is not None
    assert graph.last_inputs["quoted_message_id"] == "wamid.receipt.42"
    assert graph.last_inputs["has_quote"] is True


@pytest.mark.asyncio
async def test_graph_handler_resume_flow_clears_quoted_message_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        whatsapp_client=SimpleNamespace(),
        queue=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )

    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    await handler.resume_flow(
        phone_number="2348000000001",
        payload={"pin_verified": True, "flow_type": "transfer"},
        channel="whatsapp",
    )

    assert graph.last_inputs is not None
    assert graph.last_inputs["quoted_message_id"] is None
    assert graph.last_inputs["has_quote"] is False
