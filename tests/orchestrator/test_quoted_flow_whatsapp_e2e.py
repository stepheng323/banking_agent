from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.core.src.agent.graphs.support.resolver import TransactionResolver
from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_support_task,
)
from apps.core.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner import plan_tasks
from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from apps.core.src.queue_consumers.outbox_consumer import OutboxConsumer
from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.queue.messages import ACTIONABLE_MESSAGES_QUEUE
from shared.types.quoted_replay import QuotedReplayInterpretation


class _InMemoryQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue(self, queue_name: str, message: dict) -> None:
        self.enqueued.append((queue_name, message))


class _MessagingClientStub:
    supports_flows = True

    def __init__(self, image_ids: list[str] | None = None, text_ids: list[str] | None = None) -> None:
        self._image_ids = list(image_ids or ["wamid.receipt.1"])
        self._text_ids = list(text_ids or ["wamid.text.1"])

    async def send_image_data(self, to: str, data: bytes, caption: str, mime_type: str) -> dict:
        del to, data, caption, mime_type
        msg_id = self._image_ids.pop(0) if self._image_ids else "wamid.receipt.default"
        return {"messages": [{"id": msg_id}]}

    async def send_text(self, to: str, text: str) -> dict:
        del to, text
        msg_id = self._text_ids.pop(0) if self._text_ids else "wamid.text.default"
        return {"messages": [{"id": msg_id}]}


class _InMemoryUsersRepo:
    def __init__(self, users_by_identity: dict[tuple[str, str], SimpleNamespace]) -> None:
        self.users_by_identity = users_by_identity

    async def get_by_channel_identity(self, channel: str, channel_user_id: str) -> SimpleNamespace | None:
        return self.users_by_identity.get((channel, channel_user_id))


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
                and row.expires_at > datetime.utcnow()
            ):
                return row
        return None


class _InMemoryUnitOfWork:
    def __init__(
        self,
        store: list[ActionableMessage],
        users_by_identity: dict[tuple[str, str], SimpleNamespace],
    ) -> None:
        self.users = _InMemoryUsersRepo(users_by_identity)
        self.actionable_messages = _InMemoryActionableRepo(store)
        self.committed = False

    async def __aenter__(self) -> "_InMemoryUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        del exc_type, exc_val, exc_tb
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return


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


class _ResolverBackedSupportWorker:
    def __init__(self, resolver: TransactionResolver) -> None:
        self.resolver = resolver
        self.last_method: str | None = None

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> SupportResult:
        del user_message, pin_verified
        tx, method = await self.resolver.resolve(
            user_id=context.get("user_id"),
            tx_ref=None,
            quoted_message_id=payload.get("quoted_message_id"),
        )
        self.last_method = method
        if tx:
            return SupportResult(outcome=SupportOutcome.OK, response=f"resolved:{method}")
        return SupportResult(outcome=SupportOutcome.NEEDS_INPUT, response=f"resolved:{method}")


def _receipt_intent(transaction_ref: str) -> dict:
    return {
        "type": "show_receipt",
        "task_id": transaction_ref,
        "receipt": {"image_base64": "cG5nLWJ5dGVz", "mime_type": "image/png"},
        "caption": f"Transfer Receipt: {transaction_ref}",
        "actionable_payload": {"transaction_id": transaction_ref},
    }


def _find_actionable_jobs(queue: _InMemoryQueue) -> list[dict]:
    return [message for queue_name, message in queue.enqueued if queue_name == ACTIONABLE_MESSAGES_QUEUE]


@pytest.mark.asyncio
async def test_receipt_intent_enqueues_actionable_jobs_for_each_sent_message() -> None:
    queue = _InMemoryQueue()
    client = _MessagingClientStub(
        image_ids=["wamid.receipt.101"],
        text_ids=["wamid.text.101"],
    )
    consumer = OutboxConsumer(
        redis_queue=queue,  # type: ignore[arg-type]
        messaging_clients={"whatsapp": client},  # type: ignore[arg-type]
    )

    await consumer.process_job(
        {
            "phone_number": "2348000000001",
            "channel": "whatsapp",
            "intents": [
                _receipt_intent("tx-101"),
                {"type": "say", "text": "Done"},
            ],
        }
    )

    actionable_jobs = _find_actionable_jobs(queue)
    assert len(actionable_jobs) == 2
    assert {job["message_id"] for job in actionable_jobs} == {"wamid.receipt.101", "wamid.text.101"}
    for job in actionable_jobs:
        assert job["channel"] == "whatsapp"
        assert job["phone_number"] == "2348000000001"
        assert job["payload"] == {"transaction_id": "tx-101"}


@pytest.mark.asyncio
async def test_non_actionable_intent_does_not_enqueue_actionable_job() -> None:
    queue = _InMemoryQueue()
    client = _MessagingClientStub(text_ids=["wamid.text.201"])
    consumer = OutboxConsumer(
        redis_queue=queue,  # type: ignore[arg-type]
        messaging_clients={"whatsapp": client},  # type: ignore[arg-type]
    )

    await consumer.process_job(
        {
            "phone_number": "2348000000002",
            "channel": "whatsapp",
            "intents": [{"type": "say", "text": "Hello there"}],
        }
    )

    actionable_jobs = _find_actionable_jobs(queue)
    assert actionable_jobs == []


@pytest.mark.asyncio
async def test_actionable_persistence_then_support_resolves_quoted_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Step A: receipt outbox -> actionable queue
    queue = _InMemoryQueue()
    outbox_consumer = OutboxConsumer(
        redis_queue=queue,  # type: ignore[arg-type]
        messaging_clients={"whatsapp": _MessagingClientStub(image_ids=["wamid.receipt.301"])},  # type: ignore[arg-type]
    )
    tx_id = str(uuid4())
    await outbox_consumer.process_job(
        {
            "phone_number": "2348000000003",
            "channel": "whatsapp",
            "intents": [_receipt_intent(tx_id)],
        }
    )
    actionable_jobs = _find_actionable_jobs(queue)
    assert len(actionable_jobs) == 1
    actionable_job = actionable_jobs[0]

    # Step B: actionable queue -> actionable persistence
    store: list[ActionableMessage] = []
    user_id = uuid4()
    users = {("whatsapp", "2348000000003"): SimpleNamespace(id=user_id)}
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.actionable_consumer.UnitOfWork",
        lambda: _InMemoryUnitOfWork(store=store, users_by_identity=users),
    )
    actionable_consumer = ActionableMessageConsumer(redis_queue=SimpleNamespace())
    await actionable_consumer.process_job(actionable_job)
    assert len(store) == 1
    assert store[0].message_type == ActionableMessageTypeEnum.TRANSFER_RECEIPT.value
    assert store[0].message_data["transaction_id"] == tx_id

    # Idempotent continuity: same job processed again should not duplicate row.
    await actionable_consumer.process_job(actionable_job)
    assert len(store) == 1

    # Step C: quoted support execution resolves via quoted path
    tx_obj = SimpleNamespace(id=uuid4())
    resolver = TransactionResolver(
        transaction_repo=_InMemoryTransactionRepo(by_id={tx_id: tx_obj}),
        actionable_message_repo=_InMemoryActionableRepo(store),
    )
    support_worker = _ResolverBackedSupportWorker(resolver)
    task = TaskSpec(id="s1", type="support", stage=TaskStage.DRAFT, payload={"intent": "transfer_status"})
    state = OrchestratorState(
        user_id=str(user_id),
        phone_number="2348000000003",
        channel="whatsapp",
        quoted_message_id=actionable_job["message_id"],
        last_message_text="why did this transfer fail?",
        loaded_context={"language": "en", "user_id": str(user_id), "profile": {"email": "u@example.com"}},
        tasks={task.id: task},
        waves=[[task.id]],
    )
    ctx = ExecutionContext(
        state=state,
        config={"configurable": {}},
        services={"support": support_worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )

    await handle_support_task(task, task.id, ctx)

    assert support_worker.last_method == "quoted"
    assert task.stage == TaskStage.COMPLETED


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
            expires_at=datetime.utcnow().replace(year=2099),
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
            expires_at=datetime.utcnow().replace(year=2099),
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

    async def interpret_quoted_replay(self, phone_number: str, text: str, context: str = "None") -> QuotedReplayInterpretation:
        del phone_number, text, context
        return self.interpretation

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> Any:
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
            expires_at=datetime.utcnow().replace(year=2099),
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
            expires_at=datetime.utcnow().replace(year=2099),
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
