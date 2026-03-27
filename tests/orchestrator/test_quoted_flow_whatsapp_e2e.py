from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.core.src.agent.graphs.support.resolver import TransactionResolver
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.types.quoted_replay import QuotedReplayInterpretation


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _InMemoryActionableRepo:
    def __init__(self, store: list[ActionableMessage]) -> None:
        self.store = store
        self.db = self

    def add(self, model: ActionableMessage) -> None:
        self.store.append(model)

    async def get_by_channel_message_id_for_user(
        self,
        channel_message_id: str,
        user_id: str,
    ) -> ActionableMessage | None:
        for row in self.store:
            if (
                row.channel_message_id == channel_message_id
                and str(row.user_id) == str(user_id)
                and row.expires_at > _utc_now_naive()
            ):
                return row
        return None


class _InMemoryTransactionRepo:
    def __init__(
        self,
        by_id: dict[str, SimpleNamespace] | None = None,
        by_idempotency_key: dict[str, SimpleNamespace] | None = None,
    ) -> None:
        self.by_id = by_id or {}
        self.by_idempotency_key = by_idempotency_key or {}
        self.lookup_order: list[tuple[str, str]] = []

    async def get_by_id(self, transaction_id: str) -> SimpleNamespace | None:
        self.lookup_order.append(("id", transaction_id))
        return self.by_id.get(transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        self.lookup_order.append(("idempotency", idempotency_key))
        return self.by_idempotency_key.get(idempotency_key)

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[SimpleNamespace]:
        del user_id, limit
        return []

    async def get_by_status(self, user_id: str, status: str) -> list[SimpleNamespace]:
        del user_id, status
        return []


@pytest.mark.asyncio
async def test_quoted_lookup_isolation_prevents_cross_user_hydration() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    quoted_message_id = "wamid.receipt.foreign"
    store = [
        ActionableMessage(
            user_id=owner_user_id,
            channel_message_id=quoted_message_id,
            message_type=ActionableMessageTypeEnum.TRANSFER_RECEIPT.value,
            message_data={"transaction_id": str(uuid4())},
            expires_at=_utc_now_naive().replace(year=2099),
        )
    ]

    resolver = TransactionResolver(
        transaction_repo=_InMemoryTransactionRepo(),
        actionable_message_repo=_InMemoryActionableRepo(store),
    )
    tx, method = await resolver.resolve(
        user_id=str(other_user_id),
        tx_ref=None,
        quoted_message_id=quoted_message_id,
    )

    assert tx is None
    assert method == "not_found"


@pytest.mark.asyncio
async def test_non_uuid_transaction_reference_falls_back_to_idempotency_key() -> None:
    user_id = uuid4()
    quoted_message_id = "wamid.receipt.idem"
    non_uuid_ref = "idem-777"
    tx_obj = SimpleNamespace(id=uuid4())
    store = [
        ActionableMessage(
            user_id=user_id,
            channel_message_id=quoted_message_id,
            message_type=ActionableMessageTypeEnum.TRANSFER_RECEIPT.value,
            message_data={"transaction_id": non_uuid_ref},
            expires_at=_utc_now_naive().replace(year=2099),
        )
    ]
    tx_repo = _InMemoryTransactionRepo(
        by_id={},
        by_idempotency_key={non_uuid_ref: tx_obj},
    )

    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_InMemoryActionableRepo(store),
    )
    tx, method = await resolver.resolve(
        user_id=str(user_id),
        tx_ref=None,
        quoted_message_id=quoted_message_id,
    )

    assert tx == tx_obj
    assert method == "quoted"
    assert tx_repo.lookup_order == [("id", non_uuid_ref), ("idempotency", non_uuid_ref)]


class _ReplayPlannerStub:
    def __init__(self, interpretation: QuotedReplayInterpretation) -> None:
        self.interpretation = interpretation
        self.plan_called = False

    async def interpret_quoted_replay(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
    ) -> QuotedReplayInterpretation:
        del phone_number, text, context
        return self.interpretation

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> Any:
        del phone_number, text, context
        self.plan_called = True
        return None


@pytest.mark.asyncio
async def test_quoted_replay_single_executes_as_direct_transfer_task() -> None:
    user_id = uuid4()
    quoted_message_id = "wamid.receipt.replay.single.direct"
    store = [
        ActionableMessage(
            user_id=user_id,
            channel_message_id=quoted_message_id,
            message_type=ActionableMessageTypeEnum.TRANSFER_RECEIPT.value,
            message_data={
                "task_type": "transfer",
                "action": "send_money",
                "amount": 5000,
                "recipient_name": "Ada",
                "recipient_account": "1234567890",
                "recipient_bank_code": "999",
                "recipient_bank_name": "GTBank",
                "source_bank_name": "Access Bank",
                "narration": "Replay test",
            },
            expires_at=_utc_now_naive().replace(year=2099),
        )
    ]
    planner = _ReplayPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.95,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "action": "send_money",
                            "amount": 50000,
                            "recipient_name": "Ada",
                            "recipient_account": "1234567890",
                            "recipient_bank_code": "999",
                            "recipient_bank_name": "GTBank",
                            "source_bank_name": "Access Bank",
                            "narration": "Replay test",
                        },
                    }
                ],
            }
        )
    )

    state = OrchestratorState(
        user_id=str(user_id),
        phone_number="2348000000004",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id=quoted_message_id,
        last_message_text="again but 50k",
        loaded_context={"language": "en", "user_id": str(user_id)},
    )

    updates = await plan_tasks(
        state,
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _InMemoryActionableRepo(store),
            }
        },
    )

    assert planner.plan_called is False
    assert "tasks" in updates
    assert len(updates["tasks"]) == 1
    next_task = next(iter(updates["tasks"].values()))
    assert next_task.type == "transfer"
    assert next_task.payload["amount"] == 50000
    assert next_task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_quoted_replay_multi_executes_as_direct_domain_tasks() -> None:
    user_id = uuid4()
    quoted_message_id = "wamid.receipt.replay.multi.direct"
    store = [
        ActionableMessage(
            user_id=user_id,
            channel_message_id=quoted_message_id,
            message_type=ActionableMessageTypeEnum.TRANSFER_RECEIPT.value,
            message_data={
                "task_type": "transfer",
                "action": "send_money",
                "amount": 5000,
                "recipient_name": "Ada",
                "recipient_account": "1234567890",
                "recipient_bank_code": "999",
                "recipient_bank_name": "GTBank",
                "source_bank_name": "Access Bank",
                "narration": "Replay test",
            },
            expires_at=_utc_now_naive().replace(year=2099),
        )
    ]
    planner = _ReplayPlannerStub(
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "action": "send_money",
                            "amount": 5000,
                            "recipient_name": "Ada",
                            "recipient_account": "1234567890",
                            "recipient_bank_code": "999",
                            "recipient_bank_name": "GTBank",
                            "source_bank_name": "Access Bank",
                        },
                    },
                    {
                        "task_type": "airtime",
                        "payload": {
                            "action": "buy_airtime",
                            "amount": 1000,
                            "recipient_phone": "08011112222",
                            "network": "MTN",
                        },
                    },
                ],
            }
        )
    )

    state = OrchestratorState(
        user_id=str(user_id),
        phone_number="2348000000005",
        channel="whatsapp",
        has_quote=True,
        quoted_message_id=quoted_message_id,
        last_message_text="again and send to tolu",
        loaded_context={"language": "en", "user_id": str(user_id)},
    )

    updates = await plan_tasks(
        state,
        {
            "configurable": {
                "task_planner": planner,
                "redis_client": None,
                "actionable_message_repo": _InMemoryActionableRepo(store),
            }
        },
    )

    assert planner.plan_called is False
    assert "tasks" in updates
    assert len(updates["tasks"]) == 2
    types = {task.type for task in updates["tasks"].values()}
    assert types == {"transfer", "airtime"}
