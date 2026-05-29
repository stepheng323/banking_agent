from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.airtime.models.types import AirtimeContext, AirtimeGates, AirtimePayload
from apps.chat.src.agent.workers.airtime.nodes.execution import ExecutionStep


class _TransactionRepoStub:
    created_kwargs: dict[str, object] | None = None

    async def get_by_idempotency_key(self, key: str) -> object | None:
        return None

    async def create(self, **kwargs: object) -> object:
        type(self).created_kwargs = kwargs
        return SimpleNamespace(id="tx-airtime-1")


class _UnitOfWorkStub:
    def __init__(self) -> None:
        self.transactions = _TransactionRepoStub()
        self.commit = AsyncMock()

    async def __aenter__(self) -> "_UnitOfWorkStub":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_airtime_execution_publishes_channel_identity_and_processing_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TransactionRepoStub.created_kwargs = None
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", _UnitOfWorkStub)

    publisher = SimpleNamespace(publish=AsyncMock())
    step = ExecutionStep()
    payload = AirtimePayload(
        amount=2000,
        recipient_phone="08031234567",
        recipient_name="Tolu",
        network="MTN",
        source_account_id="acc-1",
        source_account_number="1234567890",
        idempotency_key="airtime-idem-1",
    )
    context = AirtimeContext(phone_number="2348162511023", language="en", channel="telegram")
    gates = AirtimeGates(pin_verified=True, confirmation_confirmed=True)
    worker_context = SimpleNamespace(user_id="user-1", publisher=publisher, channel_identity="927331985")

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.receipt is not None
    assert result.receipt["status"] == "processing"
    assert result.receipt["message"] == (
        "Your airtime purchase of ₦2,000.00 for Tolu (08031234567) (MTN) is being processed."
    )

    publish_message = publisher.publish.await_args.kwargs["message"]
    assert publish_message["channel"] == "telegram"
    assert publish_message["phone_number"] == "2348162511023"
    assert publish_message["channel_identity"] == "927331985"
    assert publish_message["airtime_data"]["phone_number"] == "08031234567"
    assert publish_message["airtime_data"]["recipient_name"] == "Tolu"

    create_kwargs = _TransactionRepoStub.created_kwargs or {}
    assert create_kwargs["target_phone_number"] == "08031234567"
    assert create_kwargs["mobile_network"] == "MTN"
    assert create_kwargs["service_metadata"] == {"recipient_name": "Tolu"}
    assert create_kwargs.get("recipient_account_number") is None
    assert create_kwargs.get("recipient_bank_code") is None
    assert create_kwargs.get("recipient_bank_name") is None
    assert create_kwargs.get("recipient_name") is None
