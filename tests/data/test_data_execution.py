from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.nodes.execution import ExecutionStep


class _TransactionRepoStub:
    created_kwargs: dict[str, object] | None = None

    async def get_by_idempotency_key(self, key: str) -> object | None:
        return None

    async def create(self, **kwargs: object) -> object:
        type(self).created_kwargs = kwargs
        return SimpleNamespace(id="tx-data-1")


class _UnitOfWorkStub:
    def __init__(self) -> None:
        self.transactions = _TransactionRepoStub()
        self.commit = AsyncMock()

    async def __aenter__(self) -> "_UnitOfWorkStub":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_data_execution_writes_mobile_biller_fields_without_transfer_recipient_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TransactionRepoStub.created_kwargs = None
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", _UnitOfWorkStub)

    publisher = SimpleNamespace(publish=AsyncMock())
    step = ExecutionStep()
    payload = DataPayload(
        amount=3500,
        target_phone="08162511023",
        network="MTN",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
        biller_code="BIL104",
        plan_size_gb=5,
        plan_validity_days=30,
        plan_tags=["monthly"],
        recipient_name="Tolu",
        source_account_id="acc-1",
        source_account_number="1234567890",
        idempotency_key="data-idem-1",
    )
    context = DataContext(phone_number="2348162511023", language="en", channel="telegram")
    gates = DataGates(pin_verified=True, confirmation_confirmed=True)
    worker_context = SimpleNamespace(user_id="user-1", publisher=publisher, channel_identity="927331985")

    result = await step.run(payload, context, gates, worker_context)

    assert result is not None
    assert result.outcome == TransactionOutcome.OK

    create_kwargs = _TransactionRepoStub.created_kwargs or {}
    assert create_kwargs["target_phone_number"] == "08162511023"
    assert create_kwargs["mobile_network"] == "MTN"
    assert create_kwargs["biller_code"] == "BIL104"
    assert create_kwargs["biller_item_code"] == "MD501"
    assert create_kwargs["biller_item_name"] == "MTN 5 GB data bundle"
    assert create_kwargs["service_metadata"] == {
        "size_gb": 5.0,
        "validity_days": 30,
        "tags": ["monthly"],
        "recipient_name": "Tolu",
    }
    assert create_kwargs.get("recipient_account_number") is None
    assert create_kwargs.get("recipient_bank_code") is None
    assert create_kwargs.get("recipient_bank_name") is None
    assert create_kwargs.get("recipient_name") is None

    publish_message = publisher.publish.await_args.kwargs["message"]
    assert publish_message["data_purchase"]["biller_code"] == "BIL104"
    assert publish_message["data_purchase"]["recipient_name"] == "Tolu"
